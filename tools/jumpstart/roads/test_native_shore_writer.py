"""A profile-clear shore cannot authorize water fills on the native lattice."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from crossing_physical import CoverageError
from shore_road_preview import NativeShorePreview
from test_native_shore_preview import mixed_channel_fixture, native_call


class NativeShoreWriterTests(unittest.TestCase):
    def preview(self, *, inlet=False, missing=False):
        preview = NativeShorePreview(native_writer=True)
        kernel = preview._prepare()
        import ribbon

        def patches(_field, zones):
            if missing:
                raise CoverageError('missing measured integer shore zone')

            def ground(x, z):
                if inlet and x == 2. and z == -24.:
                    return 29.9
                return 30.6 + .2 * np.clip(-z - 6., 0., 4.)

            return {f'z_{x}_{z}': ribbon._flat_patch(x, z, ground)
                    for x, z in zones}

        kernel.writer_patches = patches
        # This constructed fixture has no POI population; the real stamp,
        # water-sign, rim and collar logic are never replaced.
        kernel.writer_keepouts = lambda zones: []
        return preview

    def test_integer_inlet_refuses_a_profile_clear_shore(self):
        graph, surface, source = mixed_channel_fixture(inland_channel=False)
        before = native_call(NativeShorePreview(), graph, surface, source)
        self.assertEqual(before['native_approach']['residual_infeasible'], [])
        self.assertEqual(before['native_approach']['profile_constraints']['verdict'], 'CLEAR')
        self.assertIsNone(native_call(self.preview(inlet=True), graph, surface, source))

    def test_dry_integer_terrain_keeps_the_owned_landing_and_writer_receipt(self):
        graph, surface, source = mixed_channel_fixture(inland_channel=False)
        preview = self.preview()
        original_path = list(sys.path)
        forbids = graph.corr.forbid.copy()
        result = native_call(preview, graph, surface, source)
        record = result['native_approach']
        self.assertEqual(record['writer_water']['verdict'], 'CLEAR')
        self.assertEqual(record['writer_water']['drained'], 0)
        self.assertEqual(record['writer_water']['steps'], 0)
        points = np.asarray(record['nodes'])
        heights = np.asarray(record['profile_y'])
        landing = points[:, 1] >= -6.
        np.testing.assert_allclose(heights[landing], 30.6, atol=.0005)
        np.testing.assert_array_equal(points[-1], [0., 0.])
        np.testing.assert_array_equal(graph.corr.forbid, forbids)
        self.assertEqual(sys.path, original_path)

    def test_reroutable_integer_inlet_does_not_discard_the_owned_shore(self):
        graph, surface, source = mixed_channel_fixture(inland_channel=False)
        graph.corr.forbid.fill(False)
        graph.ok = np.ones_like(graph.corr.forbid)
        physical = graph.corr.forbid.copy()
        result = native_call(self.preview(inlet=True), graph, surface, source)
        self.assertIsNotNone(result)
        record = result['native_approach']
        self.assertEqual(record['writer_water']['verdict'], 'CLEAR')
        self.assertEqual(record['writer_water']['drained'], 0)
        self.assertEqual(record['nodes'][-1], [0., 0.])
        self.assertEqual(record['residual_infeasible'], [])
        np.testing.assert_array_equal(graph.corr.forbid, physical)

    def test_initial_native_build_restores_a_cold_import_path(self):
        graph, surface, source = mixed_channel_fixture(inland_channel=False)
        preview = NativeShorePreview()
        preview._prepare()
        with patch.dict(sys.modules):
            sys.modules.pop('ribbon', None)
            original_path = list(sys.path)
            result = native_call(preview, graph, surface, source)
            self.assertEqual(sys.path, original_path)
            self.assertEqual(result['native_approach']['nodes'][-1], [0., 0.])

    def test_unread_retry_ground_is_not_a_physical_shore_refusal(self):
        graph, surface, source = mixed_channel_fixture(inland_channel=False)
        graph.corr.forbid.fill(False)
        graph.ok = np.ones_like(graph.corr.forbid)
        # The initial straight line is measured; avoiding the integer inlet
        # asks for a different part of the field, which has not been read.
        at, batch = surface.at, surface.sample_points
        surface.at = lambda x, z: at(x, z) if abs(x) <= 3.5 else (np.nan, 0.)
        surface.sample_points = lambda points: np.where(
            abs(points[:, 0]) <= 3.5, batch(points), np.nan)
        self.assertIsNotNone(native_call(NativeShorePreview(), graph, surface, source))
        with self.assertRaises(CoverageError):
            native_call(self.preview(inlet=True), graph, surface, source)

    def test_missing_integer_measurements_do_not_fall_back_to_smooth_field(self):
        graph, surface, source = mixed_channel_fixture(inland_channel=False)
        with self.assertRaisesRegex(CoverageError, 'missing measured integer shore zone'):
            native_call(self.preview(missing=True), graph, surface, source)


if __name__ == '__main__':
    unittest.main()
