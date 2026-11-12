"""A road ending at its destination centre still has a walked arrival."""
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import ribbon as R


class CentredArrivalTests(unittest.TestCase):
    def setUp(self):
        self.pads = R.pad_keepouts_from_chain([], [], planned=[{
            'site_id': 'destination', 'name': 'destination',
            'x': 0., 'z': 0., 'hw': 4., 'hd': 4., 'datum_y': 40.}])

    def segment(self, nodes):
        return dict(id='approach', nodes=nodes, width_m=2.,
                    profile_y=[40.] * len(nodes),
                    is_bridge=[False] * len(nodes))

    def test_both_centred_ends_probe_along_their_arriving_bearing(self):
        for nodes, end, sample in (
                ([[-30., 0.], [0., 0.]], 'B', (1, 0)),
                ([[0., 0.], [30., 0.]], 'A', (-1, 0))):
            with self.subTest(end=end):
                walks = self.pads.arrival_walks(self.segment(nodes))
                self.assertEqual([w['end'] for w in walks], [end])
                self.assertIn(sample, walks[0]['vertices'])

    def test_curved_road_uses_terminal_tangent_not_far_endpoint(self):
        seg = self.segment([[-30., -30.], [0., -10.], [0., 0.]])
        walks = self.pads.arrival_walks(seg)
        self.assertEqual([w['end'] for w in walks], ['B'])
        self.assertIn((0, 1), walks[0]['vertices'])
        self.assertTrue(all(x == 0 for x, _ in walks[0]['vertices']))

    def test_actual_step_on_centred_arrival_is_not_silently_accepted(self):
        seg = self.segment([[-30., 0.], [0., 0.]])
        flat = self.pads.arrivals(seg, lambda _x, _z: 40.)
        self.assertEqual(flat, [])
        stepped = self.pads.arrivals(seg, lambda x, _z: 40. if x < 1 else 41.)
        self.assertTrue(any(f['check'] == 'terminus_step' and
                            f['destination'] == 'destination'
                            for f in stepped), stepped)


if __name__ == '__main__':
    unittest.main()
