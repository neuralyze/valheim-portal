"""Real native mixed-road profiles, not callback/API acceptance substitutes."""
from pathlib import Path
import copy
import json
import sys
from types import SimpleNamespace
import unittest

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import shores as S
from shore_road_preview import NativeShorePreview
from crossing_physical import CoverageError


SOURCE_ID = 'ferry-eastisle-inland'
TERMINAL_ID = 'ferry-terminal-eastisle'


def mixed_channel_fixture(*, forbidden=False, surface_channel=True, inland_channel=True,
                          cell_m=1.):
    """A dry fixed landing, then an unavoidable bridgeable inland channel.

    Search cells are confined to a straight lane; its width-aware physical
    probes still read the actual measured shoulders. There is no dry detour.
    The optional overlay puts the channel only in the supplied surface, not
    the graph field, to detect bare-field substitution by physical profiling.
    """
    n = 257
    axis = np.arange(n, dtype=float) - n // 2
    _xx, zz = np.meshgrid(axis, axis)
    heights = np.full((n, n), 31.4, dtype=np.float32)
    # The actual owned six-metre landing is at 30.6; its inland transition
    # rises at 20%, below the unchanged construction ceiling.
    shore = zz >= -10.
    heights[shore] = 30.6 + .2 * np.clip(-zz[shore] - 6., 0., 4.)
    if inland_channel:
        heights[(zz >= -64.) & (zz <= -44.)] = 28.
    measured = S.W.Field('mixed-channel', 0., 0., n / 2, 1., n, heights,
                         np.ones((n, n), dtype=np.uint8))
    graph_heights = heights.copy()
    if not surface_channel:
        graph_heights[(zz >= -64.) & (zz <= -44.)] = 31.4
    native = S.F.Field(measured.id, measured.cx, measured.cz, measured.half,
                       measured.step, measured.n, graph_heights, measured.biomes)
    corr = S.route.Corridor(native, cell_m=cell_m, grade_max=S.route.GRADE_CEILING)
    forbidden_cells = np.ones((corr.m, corr.m), dtype=bool)
    _row, col = corr.cell_of(0., -100.)
    forbidden_cells[:, col] = False
    if forbidden:
        row, _col = corr.cell_of(0., -54.)
        forbidden_cells[row, :] = True
    corr.forbid = forbidden_cells.copy()
    corr.guidance = np.full((corr.m, corr.m), .125, dtype=float)
    graph = SimpleNamespace(corr=corr, ok=~forbidden_cells, cls='spur')
    source = dict(xz=(0., -100.), island='numeric', patch=measured.id)
    return graph, S.GeneratedSurface({measured.id: measured}), source


def native_call(preview, graph, surface, source, **kwargs):
    return preview(graph=graph, surf=surface, source_id=SOURCE_ID,
                   source_node=source, terminal_id=TERMINAL_ID,
                   root=(0., 0.), bearing=0., width_m=4., level_y=30.6, **kwargs)


class NativeShorePreviewTests(unittest.TestCase):
    def test_unavoidable_inland_channel_uses_native_crossing_and_profile(self):
        graph, surface, source = mixed_channel_fixture()
        self.assertTrue(S.approach(surface, (0., 0.), 0., 4.))
        self.assertIsNone(S.road_preview(graph, surface, source, (0., 0.), 0., 4.))
        result = native_call(NativeShorePreview(), graph, surface, source)
        self.assertIsNotNone(result)
        record = result['native_approach']
        self.assertTrue(record['crossings'])
        self.assertGreater(record['bridged_stations'], 0)
        self.assertEqual(record['residual_infeasible'], [])
        self.assertEqual(record['profile_constraints']['verdict'], 'CLEAR')
        self.assertEqual(record['self_adjacency']['verdict'], 'CLEAR')
        self.assertEqual(record['census']['verdict'], 'PROVISIONAL')
        self.assertEqual(len(record['profile_y']), len(record['nodes']))
        self.assertEqual(len(record['is_bridge']), len(record['nodes']))
        self.assertEqual(record['nodes'][-1], [0., 0.])
        points = np.asarray(record['nodes'])
        profile = np.asarray(record['profile_y'])
        arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        last = arcs >= arcs[-1] - S.BANK_LEVEL_M - 1e-8
        np.testing.assert_allclose(profile[last], 30.6, atol=.0005)
        np.testing.assert_allclose(points[last, 0], 0., atol=1e-8)
        self.assertFalse(np.any(np.asarray(record['is_bridge'])[last]))
        bridge = np.flatnonzero(record['is_bridge'])
        self.assertTrue(np.all(points[bridge, 1] < -S.BANK_LEVEL_M))
        self.assertTrue(all(c['span_m'] <= 96. for c in record['crossings']))
        grade = abs(np.diff(profile)) / np.diff(arcs)
        self.assertLessEqual(float(grade.max()), record['grade_limit'] + .0011)
        scope = record['preflight_scope']
        self.assertEqual(scope['kind'], 'local measured approach preflight')
        self.assertIn('native writer', scope['authority_pending'])
        self.assertIn('saved world', scope['authority_pending'])
        json.dumps(result, allow_nan=False)
        yaml.safe_dump(result)

    def test_coarse_bank_contact_retains_a_feasible_native_profile(self):
        graph, surface, source = mixed_channel_fixture(
            inland_channel=False, cell_m=4.)
        result = native_call(NativeShorePreview(), graph, surface, source)
        self.assertIsNotNone(result)
        record = result['native_approach']
        self.assertEqual(record['profile_constraints']['verdict'], 'CLEAR')
        self.assertFalse(record['residual_infeasible'])
        points = np.asarray(record['nodes'])
        arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        profile = np.asarray(record['profile_y'])
        self.assertLessEqual(
            float(np.max(abs(np.diff(profile)) / np.diff(arcs))),
            record['grade_limit'] + .0011)

    def test_coarse_coast_tries_inland_contacts_without_moving_owned_root(self):
        n = 257
        axis = np.arange(n, dtype=float) - n // 2
        _xx, zz = np.meshgrid(axis, axis)
        heights = np.where(zz <= 0., 30.6 - zz * .1,
                           np.maximum(25., 30.6 - zz * .8)).astype(np.float32)
        measured = S.W.Field('coast', 0., 0., n / 2, 1., n, heights,
                             np.ones((n, n), dtype=np.uint8))
        field = S.F.Field(measured.id, measured.cx, measured.cz, measured.half,
                          measured.step, measured.n, heights, measured.biomes)
        corr = S.route.Corridor(field, cell_m=4., grade_max=S.route.GRADE_CEILING)
        graph = SimpleNamespace(corr=corr, ok=np.ones((corr.m, corr.m), dtype=bool),
                                cls='spur')
        source = dict(xz=(0., -40.), patch=measured.id, island='numeric')
        result = native_call(NativeShorePreview(), graph,
                             S.GeneratedSurface({measured.id: measured}), source)
        self.assertIsNotNone(result)
        record = result['native_approach']
        self.assertEqual(record['profile_constraints']['verdict'], 'CLEAR')
        self.assertFalse(record['residual_infeasible'])
        points = np.asarray(record['nodes'])
        profile = np.asarray(record['profile_y'])
        arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        landing = arcs >= arcs[-1] - S.BANK_LEVEL_M - 1e-8
        np.testing.assert_array_equal(points[-1], [0., 0.])
        np.testing.assert_allclose(points[landing, 0], 0., atol=1e-8)
        np.testing.assert_allclose(profile[landing], 30.6, atol=.0005)
        self.assertLessEqual(float(np.max(abs(np.diff(profile)) / np.diff(arcs))),
                             record['grade_limit'] + .0011)

    def test_forbidden_connection_refuses_without_mutating_live_state(self):
        import network as live
        graph, surface, source = mixed_channel_fixture(forbidden=True)
        graph.corr.last_terminal = {'untouched': True}
        original_source = copy.deepcopy(source)
        original_nodes = copy.deepcopy(live.specmod.NODES)
        anchors = copy.deepcopy(live._NODE_ANCHORS)
        starts = copy.deepcopy(live._ARRIVAL_STARTS)
        claims = live._PAD_CLAIMS
        datums = live._PAD_DATUMS
        forbid = graph.corr.forbid
        guidance = graph.corr.guidance
        mask = graph.ok.copy()
        preview = NativeShorePreview()
        self.assertIsNone(native_call(preview, graph, surface, source))
        self.assertEqual(source, original_source)
        self.assertEqual(live.specmod.NODES, original_nodes)
        self.assertEqual(live._NODE_ANCHORS, anchors)
        self.assertEqual(live._ARRIVAL_STARTS, starts)
        self.assertIs(live._PAD_CLAIMS, claims)
        self.assertIs(live._PAD_DATUMS, datums)
        self.assertIs(graph.corr.forbid, forbid)
        self.assertIs(graph.corr.guidance, guidance)
        self.assertEqual(graph.corr.last_terminal, {'untouched': True})
        self.assertFalse(graph.corr.forbid_all_water)
        np.testing.assert_array_equal(graph.ok, mask)
        np.testing.assert_array_equal(forbid, ~mask)

    def test_physical_sampler_not_graph_heights_decides_bridge(self):
        graph, surface, source = mixed_channel_fixture(surface_channel=False)
        self.assertGreater(graph.corr.fld.bilinear(0., -54.), S.W.WATER_LEVEL)
        self.assertLess(surface.at(0., -54.)[0], S.W.WATER_LEVEL)
        result = native_call(NativeShorePreview(), graph, surface, source)
        self.assertIsNotNone(result)
        self.assertGreater(result['native_approach']['bridged_stations'], 0)
        self.assertTrue(result['native_approach']['crossings'])

    def test_reused_kernel_does_not_retain_an_old_source_handoff_or_datum(self):
        graph, surface, source = mixed_channel_fixture()
        preview = NativeShorePreview()
        # The semantic centre stays behind the actual measured exterior contact.
        source['xz'] = (0., -110.)
        source['level_y'] = 50.
        handoff = dict(handoff_xz=[0., -100.], semantic_xz=list(source['xz']),
                       owner='numeric-pad', contact_xz=[0., -101.],
                       rooted_sample_xz=[0., -101.])
        first = native_call(preview, graph, surface, source, source_approach=handoff)
        self.assertIsNotNone(first)
        self.assertEqual(first['nodes'][0], handoff['handoff_xz'])
        self.assertIsNone(first['source_level_y'])
        self.assertEqual(first['native_approach']['preflight_scope']['source_node'],
                         json.loads(json.dumps(source)))
        kernel = preview._kernel
        # Same source identity, different endpoint and a truly impossible datum.
        # A leaked handoff/datum would incorrectly keep accepting the old road.
        second_source = dict(source, xz=(0., -96.))
        self.assertIsNone(native_call(preview, graph, surface, second_source))
        self.assertIs(preview._kernel, kernel)
        self.assertEqual(kernel.specmod.NODES[SOURCE_ID]['xz'], (0., -96.))
        self.assertNotIn(SOURCE_ID, kernel._NODE_ANCHORS)
        self.assertEqual(source['level_y'], 50.)

    def test_unread_landing_is_not_reclassified_as_a_physical_refusal(self):
        graph, surface, source = mixed_channel_fixture()
        # The real generated sampler cannot supply the shoulder beyond x=128.
        with self.assertRaises((CoverageError, SystemExit)):
            NativeShorePreview()(
                graph=graph, surf=surface, source_id=SOURCE_ID,
                source_node=source, terminal_id=TERMINAL_ID,
                root=(128., 0.), bearing=0., width_m=4., level_y=30.6)


if __name__ == '__main__':
    unittest.main()
