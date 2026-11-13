"""Residual terrain is not permission to move a bridge or abutment datum."""
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import route as R


class FixedProfileConstraintsTests(unittest.TestCase):
    def fixture(self):
        s = np.arange(5, dtype=np.float64) * 2.0
        points = np.column_stack((s, np.zeros_like(s)))
        lo = np.array([30.6, 30.6, 34.52248045737313,
                       34.52248045737313, 34.52248045737313])
        return s, points, lo, lo.copy()

    def test_residual_span_cannot_turn_owned_deck_into_ramp(self):
        s, points, lo, hi = self.fixture()
        fitted = R.fit_ladder(
            np.linspace(lo[0], lo[-1], len(s)), s, lo, hi,
            np.array([True, True, True, True, False]), [.08, .12, .18, .25],
            pts=points, width_m=6., grade=.625, batter_m=12., lattice_m=1.)
        self.assertEqual(fitted['joined']['verdict'], 'REFUSED')
        self.assertGreater(fitted['joined']['proof']['empty_stations'], 0)

    def test_joint_residual_cannot_discard_own_fixed_datums(self):
        s, points, lo, hi = self.fixture()
        bridge = np.array([True, True, True, True, False])
        leg = dict(id='owned-bridge', cls='spur', pri=1, width_m=6.,
                   nodes=points, is_bridge=bridge,
                   y0=np.linspace(lo[0], lo[-1], len(s)), s=s,
                   lo=lo, hi=hi, spans=[(0, 4)], rung=.25, ladder=[.25],
                   cand=R.self_adjacency_lattice(
                       points, bridge, 6., grade=.625, batter_m=12.,
                       lattice_m=1., dy_cap=.25))
        other_points = points + [100., 0.]
        other_y = np.full(len(s), 35.)
        other = dict(leg, id='separate-live-road', nodes=other_points,
                     y0=other_y, lo=other_y.copy(), hi=other_y.copy(), spans=[],
                     cand=R.self_adjacency_lattice(
                         other_points, bridge, 6., grade=.625, batter_m=12.,
                         lattice_m=1., dy_cap=.25))
        result = R.junction_datums(
            [leg, other], [], grade=.625, batter_m=12., lattice_m=1.)
        self.assertNotIn(leg['id'], result['y'])
        self.assertEqual(result['refused'][0]['binding']['reading'], 'own bounds')
        np.testing.assert_array_equal(result['y'][other['id']], other_y)

    def test_residual_ground_keeps_compatible_terminal_heights(self):
        s = np.arange(5, dtype=np.float64) * 2.0
        points = np.column_stack((s, np.zeros_like(s)))
        terrain = np.array([30.6, 30.6, 60., 31.6, 31.6])
        lo, hi = terrain - 7., terrain + 7.
        lo[[0, -1]] = hi[[0, -1]] = [30.6, 31.6]
        result = R.fit_ladder(
            terrain, s, lo, hi, np.zeros(len(s), dtype=bool), [.25],
            pts=points, width_m=6., grade=.625, batter_m=12., lattice_m=1.)
        self.assertFalse(result['terrain_feasible'])
        self.assertIn(result['joined']['verdict'], ('CLEAR', 'JOINED'))
        np.testing.assert_array_equal(result['y'][[0, -1]], [30.6, 31.6])
        self.assertLessEqual(float(np.max(np.abs(np.diff(result['y'])) / np.diff(s))), .25 + 1e-9)


if __name__ == '__main__':
    unittest.main()
