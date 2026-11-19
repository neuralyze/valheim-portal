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

    def test_native_heightmap_debt_is_requested_only_for_a_usable_berth(self):
        fields = constructed_coast()
        surface = S.GeneratedSurface(fields)
        # The road cap is dry, but short slips place the hull on the side shoal.
        root = (0., -3.)
        requirement = dict(id='physical-shoal', anchor='source', kind='slip',
                           cls='spur', width_tiles=2)
        nodes = {'source': dict(xz=(0., -40.), patch='coast', island='spawn')}
        progress = {}
        road_preview = S.road_preview
        vessel = S.VESSEL
        need = (S.WF.VESSELS[vessel]['draught_m'] + S.WF.KEEL_CLEARANCE_M
                + S.WF.BERTH_MARGIN_FLOOR_M)

        def check_road(graph, surf, node, root, bearing, width, **kwargs):
            bx, bz = S._boat_pose(root, bearing, progress['length'], 'slip', width, vessel)
            hull = surf.hull_depths(bearing, bx, bz, vessel)
            if not (hull and hull[2] and hull[3] and hull[0] >= need):
                raise RuntimeError('Heightmap prerequisite for an unusable berth')
            return road_preview(graph, surf, node, root, bearing, width, **kwargs)

        with (patch.object(S, '_shore_candidates', return_value=([root], None)),
              patch.object(S, 'road_preview', check_road)):
            result = S.solve_site(requirement, nodes, fields, graph_factory=plain_graph,
                                  surface=surface, progress=progress.update)
        self.assertEqual(result['root'], list(root))
        self.assertGreaterEqual(result['boat']['berth_depth_m'], need)
        self.assertTrue(result['structural']['ok'])

        def missing_road(*args, **kwargs):
            check_road(*args, **kwargs)
            raise RuntimeError('Heightmap prerequisite for a usable berth')

        with (patch.object(S, '_shore_candidates', return_value=([root], None)),
              patch.object(S, 'road_preview', missing_road),
              self.assertRaisesRegex(RuntimeError, 'Heightmap prerequisite for a usable berth')):
            S.solve_site(requirement, nodes, fields, graph_factory=plain_graph,
                         surface=surface, progress=progress.update)


if __name__ == '__main__':
    unittest.main()
