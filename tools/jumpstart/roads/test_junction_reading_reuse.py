"""Conflicts must follow the current live graph, not removed bridge legs."""
import unittest
from unittest.mock import patch

import numpy as np
from roads import network


class JunctionReadingReuseTests(unittest.TestCase):
    def test_separate_conflicts_preserve_survivors_after_connecting_leg_refuses(self):
        def leg(name, points, height, priority):
            segment, joint = network._jd_fixture(
                name, points, [height]*len(points), pri=priority, rung=.12)
            joint['lo'] = joint['y0'].copy()
            joint['hi'] = joint['y0'].copy()
            joint['ladder'] = [.12]
            return segment, joint

        fixtures = {
            'A': leg('A', [(2.*k, 0.) for k in range(21)], 30., 0),
            'B': leg('B', [(20., -20.+2.*k) for k in range(51)], 40., 2),
            'C': leg('C', [(2.*k, 60.) for k in range(21)], 40., 0),
            'D': leg('D', [(30., 40.+2.*k) for k in range(21)], 50., 1),
        }
        counts = {'visits': 0, 'rows': 0}
        class CountedReadings(np.ndarray):
            def __array_finalize__(self, parent):
                self.counter = None

            def __iter__(self):
                for value in super().__iter__():
                    if self.counter is not None:
                        self.counter['visits'] += 1
                    yield value

        original = network.routemod._joint_readings
        def measured_readings(*args, **kwargs):
            readings = original(*args, **kwargs)
            for group, keys in (('pairs', ('la', 'lb')), ('pads', ('la',))):
                for key in keys:
                    array = readings[group][key].view(CountedReadings)
                    array.counter = counts
                    counts['rows'] += len(array)
                    readings[group][key] = array
            return readings

        with patch.object(network.routemod, '_joint_readings', measured_readings):
            for order in (['A','B','C','D'], ['D','C','B','A']):
                with self.subTest(order=order):
                    segments, result = network._jd_net(fixtures, order=order)
                    self.assertEqual({row['id'] for row in result['refused']}, {'B','D'})
                    self.assertEqual({row['id'] for row in segments}, {'A','C'})
                    for name in ('A','C'):
                        np.testing.assert_array_equal(result['y'][name], fixtures[name][1]['y0'])
        # Raw readings may be traversed to prepare geometry and report the
        # result, not once for every feasibility probe or conflict walk.
        self.assertLessEqual(counts['visits'], 8*counts['rows'])


if __name__ == '__main__':
    unittest.main()
