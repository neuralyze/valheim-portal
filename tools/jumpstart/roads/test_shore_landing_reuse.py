"""A fixed generated landing must not be remeasured for every jetty length."""
from collections import Counter
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import shores as S


def constructed_coast():
    n = 1401
    axis = np.arange(n, dtype=float) - n // 2
    xx, zz = np.meshgrid(axis, axis)
    heights = np.where(zz <= 0, S.DECK_Y - zz * .1,
                       np.maximum(S.W.WATER_LEVEL - 5., S.DECK_Y - zz * .8))
    # The centre ray is deep enough, but the full hull needs a longer jetty
    # to clear these genuinely shallow side shoals. Landing terrain is fixed.
    side_shoal = (np.abs(xx) > 1.25) & (np.abs(xx) < 8.) & (zz >= 8.) & (zz <= 26.)
    heights[side_shoal] = S.W.WATER_LEVEL - .5
    field = S.W.Field('coast', 0., 0., n / 2, 1., n,
                      heights.astype(np.float32), np.ones((n, n), np.uint8))
    out = S.W.Field('outside', 1000., 1000., 100.5, 1., 201,
                   np.full((201, 201), 40., np.float32),
                   np.ones((201, 201), np.uint8))
    return {'coast': field, 'outside': out}


def plain_graph(field, cls):
    corr = S.route.Corridor(field, cell_m=4., grade_max=S.route.GRADE[cls])
    return S.geography.RoadGraph(field, cls=cls,
        forbid=np.zeros((corr.m, corr.m), dtype=bool))


class LandingReuseTests(unittest.TestCase):
    def test_generated_landing_work_is_bounded_across_actual_hull_retries(self):
        fields = constructed_coast()
        counts = Counter()
        original = S.approach

        def measured(surface, root, bearing, width, **kwargs):
            counts[tuple(root), bearing] += 1
            return original(surface, root, bearing, width, **kwargs)

        requirement = dict(id='physical-shoal', anchor='source', kind='jetty',
                           cls='spur', width_tiles=2)
        nodes = {'source': dict(xz=(0., -40.), patch='coast', island='spawn')}
        with patch.object(S, 'approach', measured):
            result = S.solve_site(requirement, nodes, fields, graph_factory=plain_graph)
        self.assertTrue(result['structural']['ok'])
        need = (S.WF.VESSELS[result['boat']['prefab']]['draught_m']
                + S.WF.KEEL_CLEARANCE_M + S.WF.BERTH_MARGIN_FLOOR_M)
        self.assertGreaterEqual(result['boat']['berth_depth_m'], need)
        self.assertTrue(result['boat']['connected'])
        self.assertLessEqual(max(counts.values()), 1)


if __name__ == '__main__':
    unittest.main()
