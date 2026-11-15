"""A roundoff-sized arc error cannot shorten the declared level bank run."""
from pathlib import Path
import math
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import crossing_physical as P
import field as F


class BankLevelRunTests(unittest.TestCase):
    @staticmethod
    def approach(*, blocked_boundary=False, distances=None):
        bank = np.array([72.3, 13.7])
        direction = np.array([math.cos(.3), math.sin(.3)])
        distances = np.arange(0., 16., 2.) if distances is None else np.asarray(distances)
        nodes = bank + distances[:, None] * direction
        axis = np.arange(-256., 257.)
        xs, zs = np.meshgrid(axis, axis)
        if blocked_boundary:
            distance = (xs-bank[0])*direction[0] + (zs-bank[1])*direction[1]
            heights = np.minimum(48.35, 44.6 + .65*distance)
        else:
            heights = np.full(xs.shape, 42.)
        field = F.Field('constructed-bank', 0., 0., 256.5, 1., len(axis),
                        heights, np.zeros(heights.shape, dtype=np.uint16))
        physical = P.PhysicalFields({'constructed-bank': field},
                                    {'seed': 'constructed-bank'}, keepout_discs=[])
        other = bank - 20.*direction
        result = physical._compile_side(
            {'id': 'level-run'}, {'xz': bank.tolist()}, {'xz': other.tolist()},
            40., {'nodes': nodes.tolist(), 'goal_xz': nodes[-1].tolist()}, 0)
        return result, nodes

    def test_published_profile_keeps_the_complete_six_metre_run(self):
        result, nodes = self.approach()
        self.assertTrue(result['ok'], result)
        segment = result['segment']
        points = np.asarray(segment['nodes'])
        arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        distances = arcs[-2] - arcs[:-1]
        boundary = int(np.argmin(abs(distances-6.)))
        self.assertAlmostEqual(distances[boundary], 6., places=12)
        self.assertAlmostEqual(segment['profile_y'][boundary], 40., places=9)
        np.testing.assert_allclose(
            np.asarray(segment['profile_y'])[:-1][distances <= 6.+1e-9],
            40., rtol=0., atol=1e-9)
        np.testing.assert_array_equal(points[:-1], nodes[::-1])

    def test_level_run_between_stations_remains_level_when_interpolated(self):
        result, _ = self.approach(distances=[0., 2., 4., 5.9, 7.9, 9.9, 11.9, 13.9])
        self.assertTrue(result['ok'], result)
        segment = result['segment']
        points = np.asarray(segment['nodes'])
        arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        at_boundary = np.interp(arcs[-2]-6., arcs, segment['profile_y'])
        self.assertAlmostEqual(at_boundary, 40., places=9)

    def test_boundary_that_cannot_hold_the_datum_is_refused_by_number(self):
        result, _ = self.approach(blocked_boundary=True)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['stage'], 'pinned_bank_constraint')
        self.assertGreater(result['footprint_high_y']-result['cut_fill_max_m'], 40.)
        self.assertEqual(result['required_y'], 40.)


if __name__ == '__main__':
    unittest.main()
