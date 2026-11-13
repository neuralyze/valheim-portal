"""Only an actual, served Crossings bank can close a road's terrain cap."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import network as N


class DeclaredTerminalCapsTests(unittest.TestCase):
    def fixture(self):
        source = dict(seed='fixture', surface='heightmap', lattice='PatchScan',
                      producer='roads.field.request', sha256='fixture-heightmap')
        proof = dict(ok=True, status='verified', source=source)
        nodes = {
            'bank-a': dict(kind='abutment', crossing='C', xz=[0., 0.], level_y=40.,
                           measured=dict(physical=proof)),
            'bank-b': dict(kind='abutment', crossing='C', xz=[20., 0.], level_y=40.,
                           measured=dict(physical=proof)),
            'inland': dict(xz=[-10., 0.])}
        crossing = dict(crossing_id='C', owner='Crossings', status='declared',
                        bank_a=dict(node='bank-a', xz=[0., 0.], road_deck_y=40.),
                        bank_b=dict(node='bank-b', xz=[20., 0.], road_deck_y=40.))
        edge = dict(id='road', a='bank-a', b='inland', cls='trunk', width_m=6.)
        points = np.array([[0., 0.], [-2., 0.], [-10., 0.]])
        heights = np.full(3, 40.)
        bridge = np.zeros(3, dtype=bool)
        bank = dict(end='a', node='bank-a', level_y=40.)
        return nodes, crossing, edge, points, heights, bridge, bank

    def test_actual_bank_has_a_closed_cap_without_an_extra_ground_endpoint(self):
        nodes, crossing, edge, points, heights, bridge, bank = self.fixture()
        original = points.copy()
        with patch.object(N.specmod, 'NODES', nodes), patch.object(N.specmod, 'DECLARED_CROSSINGS', [crossing]):
            caps, owners = N._declared_terminal_caps(edge, points, heights, bridge, [bank])
        self.assertEqual(caps, (False, True))
        self.assertEqual(owners['a']['node'], 'bank-a')
        self.assertEqual(owners['a']['opposite_node'], 'bank-b')
        np.testing.assert_array_equal(points, original)

    def test_reversed_road_closes_only_the_served_bank_end(self):
        nodes, crossing, edge, points, heights, bridge, bank = self.fixture()
        edge.update(a='inland', b='bank-a')
        bank['end'] = 'b'
        with patch.object(N.specmod, 'NODES', nodes), patch.object(N.specmod, 'DECLARED_CROSSINGS', [crossing]):
            caps, owners = N._declared_terminal_caps(edge, points[::-1], heights[::-1], bridge, [bank])
        self.assertEqual(caps, (True, False))
        self.assertEqual(set(owners), {'b'})

    def test_unserved_shifted_or_bridged_terminal_cannot_claim_ground_bank_ownership(self):
        nodes, crossing, edge, points, heights, bridge, bank = self.fixture()
        for shifted, flags, active in (
            (points + [.5, 0.], bridge, [bank]),
            (points, np.array([True, False, False]), [bank]),
            (points, bridge, [])):
            with self.subTest(shifted=shifted[0].tolist(), bridge=bool(flags[0]), active=bool(active)):
                with patch.object(N.specmod, 'NODES', nodes), patch.object(N.specmod, 'DECLARED_CROSSINGS', [crossing]):
                    caps, owners = N._declared_terminal_caps(edge, shifted, heights, flags, active)
                self.assertEqual(caps, (True, True))
                self.assertEqual(owners, {})

    def test_crossing_identity_and_bank_geometry_must_agree(self):
        nodes, crossing, edge, points, heights, bridge, bank = self.fixture()
        for field, value in (('owner', 'Other'), ('bank_a', dict(node='bank-a', xz=[1., 0.], road_deck_y=40.))):
            altered = dict(crossing, **{field: value})
            with self.subTest(field=field):
                with patch.object(N.specmod, 'NODES', nodes), patch.object(N.specmod, 'DECLARED_CROSSINGS', [altered]):
                    with self.assertRaises(ValueError):
                        N._declared_terminal_caps(edge, points, heights, bridge, [bank])

    def test_undeclared_abutment_retains_ordinary_ground_cap(self):
        nodes, crossing, edge, points, heights, bridge, bank = self.fixture()
        nodes['bank-a'].pop('crossing')
        with patch.object(N.specmod, 'NODES', nodes), patch.object(N.specmod, 'DECLARED_CROSSINGS', []):
            caps, owners = N._declared_terminal_caps(edge, points, heights, bridge, [bank])
        self.assertEqual(caps, (True, True))
        self.assertEqual(owners, {})


if __name__ == '__main__':
    unittest.main()
