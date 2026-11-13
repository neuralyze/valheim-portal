"""Numeric dry-footprint eligibility through the real shore/router instruments."""
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import shores as S


def measured_route(*, wet_x=None, inset=False, lane_only=True):
    n = 129
    axis = np.arange(n, dtype=float) - n // 2
    xx, zz = np.meshgrid(axis, axis)
    heights = np.full((n, n), S.DECK_Y, dtype=np.float32)
    wet = (xx == wet_x) & (zz >= -32.) & (zz <= -24.) if wet_x is not None else None
    if wet is not None and not inset:
        heights[wet] = S.W.WATER_LEVEL - .1
    field = S.W.Field('approach', 0., 0., n / 2, 1., n, heights,
                      np.ones((n, n), dtype=np.uint8))
    fields = {'approach': field}
    if inset:
        ni = 17
        ix, iz = np.meshgrid(np.arange(ni, dtype=float) - ni // 2,
                             np.arange(ni, dtype=float) - ni // 2 - 28.)
        ih = np.full((ni, ni), S.DECK_Y, dtype=np.float32)
        ih[(ix == wet_x) & (iz >= -32.) & (iz <= -24.)] = S.W.WATER_LEVEL - .1
        fields['inset'] = S.W.Field('inset', 0., -28., ni / 2, 1., ni, ih,
                                   np.ones((ni, ni), dtype=np.uint8))
    native = S.F.Field(field.id, field.cx, field.cz, field.half, field.step,
                       field.n, field.heights, field.biomes)
    corridor = S.route.Corridor(native, cell_m=4., grade_max=S.route.GRADE['spur'])
    forbid = np.zeros((corridor.m, corridor.m), dtype=bool)
    if lane_only:
        # A real mask, not a fake route verdict: the shoulder feedback must
        # exhaust this one-column road graph rather than jump the wet section.
        forbid[:] = True
        _row, col = corridor.cell_of(0., -48.)
        forbid[:, col] = False
    graph = S.geography.RoadGraph(native, cls='spur', forbid=forbid)
    return graph, S.GeneratedSurface(fields)


class ShoreRoadPreviewTests(unittest.TestCase):
    root = (0., 0.)
    source = dict(xz=(0., -48.))

    def test_dry_centerline_and_valid_landing_do_not_certify_farther_wet_shoulder(self):
        graph, measured = measured_route(wet_x=3.)
        self.assertTrue(S.approach(measured, self.root, 0., 4.))
        self.assertTrue(all(measured.at(0., z)[0] > S.W.WATER_LEVEL
                            for z in range(-48, 1)))
        original_mask = graph.ok.copy()
        original_forbid = graph.corr.forbid
        self.assertIsNone(S.road_preview(graph, measured, self.source,
                                         self.root, 0., 4.))
        np.testing.assert_array_equal(graph.ok, original_mask)
        self.assertIs(graph.corr.forbid, original_forbid)

    def test_requested_four_metre_width_is_not_replaced_by_six_metre_default(self):
        graph, measured = measured_route(wet_x=5.)
        self.assertTrue(S.approach(measured, self.root, 0., 4.))
        self.assertTrue(S.approach(measured, self.root, 0., 6.))
        narrow = S.road_preview(graph, measured, self.source, self.root, 0., 4.)
        self.assertIsNotNone(narrow)
        self.assertEqual(narrow['width_m'], 4.)
        self.assertIsNone(S.road_preview(graph, measured, self.source,
                                         self.root, 0., 6.))

    def test_dry_route_keeps_real_nodes_fixed_prefix_and_existing_metadata(self):
        graph, measured = measured_route()
        source = dict(self.source, level_y=S.DECK_Y + 1.)
        result = S.road_preview(graph, measured, source, self.root, 0., 4.)
        self.assertIsNotNone(result)
        self.assertTrue(all(abs(x) < 1e-12 for x, z in result['nodes']
                            if -S.BANK_LEVEL_M <= z <= 0.))
        self.assertEqual(result['nodes'][-1], list(self.root))
        self.assertEqual(result['entry_xz'], [0., -S.BANK_LEVEL_M])
        self.assertEqual(result['source_level_y'], source['level_y'])
        self.assertEqual(result['arrival_level_y'], S.DECK_Y)
        self.assertLessEqual(result['grade_max'], S.route.GRADE_CEILING)
        self.assertEqual(result['water_reroute_rounds'], 0)
        self.assertIsNone(result['routing']['refusal'])

    def test_explicit_owned_arrival_datum_is_not_replaced_by_global_deck(self):
        graph, measured = measured_route()
        datum = S.DECK_Y + 1.25
        result = S.road_preview(graph, measured, self.source,
                                self.root, 0., 4., level_y=datum)
        self.assertIsNotNone(result)
        self.assertEqual(result['arrival_level_y'], datum)

    def test_masked_authored_source_keeps_normal_allowed_source_snap(self):
        graph, measured = measured_route(lane_only=False)
        graph.ok[graph.corr.cell_of(*self.source['xz'])] = False
        result = S.road_preview(graph, measured, self.source, self.root, 0., 4.)
        self.assertIsNotNone(result)
        self.assertGreater(result['source_snap_m'], 0.)
        self.assertNotEqual(result['nodes'][0], list(self.source['xz']))

    def test_generated_surface_smallest_patch_dispatch_is_not_replaced_by_bare_field(self):
        graph, measured = measured_route(wet_x=3., inset=True)
        self.assertGreater(graph.corr.fld.bilinear(3., -28.), S.W.WATER_LEVEL)
        self.assertLess(measured.at(3., -28.)[0], S.W.WATER_LEVEL)
        self.assertTrue(S.approach(measured, self.root, 0., 4.))
        self.assertIsNone(S.road_preview(graph, measured, self.source,
                                         self.root, 0., 4.))

    def test_unconstrained_wet_shoulder_route_is_rejected_or_canonically_rerouted(self):
        graph, measured = measured_route(wet_x=3., lane_only=False)
        result = S.road_preview(graph, measured, self.source, self.root, 0., 4.)
        if result is None:
            return
        self.assertGreater(result['water_reroute_rounds'], 0)
        from crossing_contract import transverse_window
        from types import SimpleNamespace
        points, _arcs = S.route.resample(result['nodes'], spacing_m=1.)
        wet = []
        _low, _high, off = transverse_window(SimpleNamespace(
            bilinear=lambda x, z: measured.at(x, z)[0],
            contains=lambda x, z: np.isfinite(measured.at(x, z)[0])),
            points, 4., wet_at=wet)
        self.assertFalse(wet)
        self.assertFalse(off)

    def test_inland_one_metre_wet_notch_is_rerouted_not_called_a_fixed_landing_failure(self):
        from types import SimpleNamespace
        n = 65
        heights = np.full((n, n), 31., dtype=np.float32)
        heights[32 - 9, 32 + 3] = 29.
        field = S.W.Field('notched-approach', 0., 0., n / 2, 1., n, heights,
                          np.ones((n, n), dtype=np.uint8))
        native = S.F.Field(field.id, field.cx, field.cz, field.half, field.step,
                           field.n, field.heights, field.biomes)
        corr = S.route.Corridor(native, cell_m=1.)
        graph = SimpleNamespace(corr=corr, ok=np.ones((corr.m, corr.m), dtype=bool))
        measured = S.GeneratedSurface({field.id: field})
        result = S.road_preview(
            graph, measured, dict(xz=(0., -24.)), self.root, 0., 4., level_y=31.)
        self.assertIsNotNone(result)
        self.assertGreater(result['water_reroute_rounds'], 0)
        from crossing_contract import transverse_window
        points, _ = S.route.resample(result['nodes'], spacing_m=1.)
        wet = []
        transverse_window(SimpleNamespace(
            bilinear=lambda x, z: measured.at(x, z)[0],
            contains=lambda x, z: np.isfinite(measured.at(x, z)[0])),
            points, 4., wet_at=wet)
        self.assertFalse(wet)


    def test_callback_without_serving_identity_cannot_approve_wet_route(self):
        from shore_road_preview import NativeShorePreview

        native = NativeShorePreview()
        graph, measured = measured_route(wet_x=3.)
        self.assertIsNone(S.road_preview(
            graph, measured, self.source, self.root, 0., 4.,
            native_preview=native))

    def test_serving_context_recovers_real_inland_bridge_after_dry_refusal(self):
        from shore_road_preview import NativeShorePreview
        from test_native_shore_preview import mixed_channel_fixture


        graph, measured, source = mixed_channel_fixture()
        original_source = dict(source)
        original_mask = graph.ok.copy()
        original_forbid = graph.corr.forbid
        self.assertIsNone(S.road_preview(
            graph, measured, source, self.root, 0., 4.))
        native = NativeShorePreview()
        result = S.road_preview(
            graph, measured, source, self.root, 0., 4.,
            native_preview=native, source_id='ferry-eastisle-inland',
            terminal_id='ferry-terminal-eastisle')
        self.assertIsNotNone(result)
        self.assertEqual(source, original_source)
        np.testing.assert_array_equal(graph.ok, original_mask)
        self.assertIs(graph.corr.forbid, original_forbid)
        self.assertEqual(result['nodes'][-1], list(self.root))
        self.assertEqual(result['arrival_level_y'], S.DECK_Y)
        self.assertLessEqual(result['grade_max'], S.route.GRADE_CEILING)
        record = result['native_approach']
        self.assertTrue(record['crossings'])
        self.assertGreater(record['bridged_stations'], 0)
        self.assertEqual(len(record['profile_y']), len(record['nodes']))
        self.assertEqual(len(record['is_bridge']), len(record['nodes']))
        self.assertEqual(record['profile_constraints']['verdict'], 'CLEAR')
        self.assertEqual(record['self_adjacency']['verdict'], 'CLEAR')
        self.assertFalse(record['residual_infeasible'])
        wet = [i for i, (x, z) in enumerate(record['nodes'])
               if measured.at(x, z)[0] <= S.W.WATER_LEVEL]
        self.assertTrue(wet)
        self.assertTrue(all(record['is_bridge'][i] for i in wet))
        scope = record['preflight_scope']
        self.assertEqual(scope['kind'], 'local measured approach preflight')
        self.assertTrue({'full named road', 'native writer', 'census', 'saved world'}
                        <= set(scope['authority_pending']))

    def test_dry_profile_cannot_bypass_native_freeboard_between_owned_ends(self):
        from shore_road_preview import NativeShorePreview
        from test_native_shore_preview import mixed_channel_fixture, native_call

        for source_level, accepted in ((30.1, False), (30.6, True)):
            with self.subTest(source_level=source_level):
                graph, measured, source = mixed_channel_fixture(inland_channel=False)
                source.update(xz=(0., -16.), level_y=source_level)
                # The dry fit alone omits the native freeboard floor between
                # the two owned six-metre runs. Neither datum may be moved.
                self.assertIsNotNone(S.road_preview(
                    graph, measured, source, self.root, 0., 4., level_y=30.6))
                native = NativeShorePreview()
                native_result = native_call(native, graph, measured, source)
                if accepted:
                    self.assertIsNotNone(native_result)
                else:
                    self.assertIsNone(native_result)
                result = S.road_preview(
                    graph, measured, source, self.root, 0., 4., level_y=30.6,
                    native_preview=native, source_id='ferry-eastisle-inland',
                    terminal_id='ferry-terminal-eastisle')
                if accepted:
                    self.assertIsNotNone(result)
                    self.assertEqual(result['nodes'][-1], list(self.root))
                    self.assertLessEqual(result['grade_max'], S.route.GRADE_CEILING)
                else:
                    self.assertIsNone(result)




if __name__ == '__main__':
    unittest.main()
