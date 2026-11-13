"""A profile-only remedy must judge the exact serialized road it was given."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from shore_road_preview import NativeShorePreview


class FixedProfileGeometryTests(unittest.TestCase):
    def test_profile_refit_keeps_rounded_owned_bank_stations(self):
        native = NativeShorePreview()._prepare()
        field = native._ww_field(lambda x, z: np.full_like(x, 35.))
        # Serialized diagonal landing: its original 6 m entry rounds just
        # beyond 6 m, so re-selecting/re-straightening its bank window drifts.
        points = np.array([[-20., -20.], [-3., -5.], [5.44, -2.54],
                           [3.63, -1.69], [1.81, -.85], [0., 0.]])
        nodes = {
            'bank': dict(xz=(0., 0.), kind='abutment', level_y=35.5,
                         island='q', patch='q'),
            'far': dict(xz=tuple(points[0]), kind='extremity', island='q', patch='q'),
        }
        with patch.object(native.specmod, 'NODES', nodes), patch.object(native, '_PAD_CLAIMS', []):
            for bank_end in ('a', 'b'):
                with self.subTest(bank_end=bank_end):
                    edge = dict(id='fixed-bank', a='far', b='bank', cls='spur', pri=2)
                    fixed = points
                    if bank_end == 'a':
                        edge['a'], edge['b'] = edge['b'], edge['a']
                        fixed = points[::-1]
                    joint = {}
                    record = native.build_segment(
                        field, edge, native._ww_corridor(field), [],
                        fixed_pts=fixed.tolist(), ceiling={}, trim_applied=True,
                        census=None, joint=joint)
                    np.testing.assert_array_equal(record['nodes'], fixed)
                    expected_arc = np.r_[0., np.cumsum(np.linalg.norm(np.diff(fixed, axis=0), axis=1))]
                    np.testing.assert_allclose(joint['s'], expected_arc, rtol=0., atol=1e-12)
                    self.assertEqual(record['profile_constraints']['verdict'], 'CLEAR')
                    self.assertEqual(record['residual_infeasible'], [])
                    self.assertAlmostEqual(record['profile_y'][0 if bank_end == 'a' else -1], 35.5)


if __name__ == '__main__':
    unittest.main()
