"""A profile-only ceiling must repair the judged geometry, not search another road."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import network as N
import ribbon as R


class ProfileCeilingGeometryTests(unittest.TestCase):
    def setUp(self):
        n = 512
        axis = np.arange(n, dtype=float) - n / 2 + .5
        x, z = np.meshgrid(axis, axis)
        # A small bay beside the middle of the judged road. The shore drops
        # at .625 grade, below the native batter grade; the dry road surface
        # is 32 m. Endpoints are far enough away that no terminal trim owns
        # these refusals. The nominal shortest road lies inland of the bay.
        shore = np.clip((30. - np.abs(x)) / 10., 0., 1.)
        heights = 32. - shore * .625 * np.clip(z - 13., 0., 4.)
        self.field = N.fieldmod.Field(
            'profile-ceiling-shore', 0., 0., n / 2, 1., n,
            heights.astype(np.float32), np.ones((n, n), dtype=np.uint8))
        self.corr = N.routemod.Corridor(
            self.field, cell_m=1., grade_max=.25, grade_weight=0.,
            roughness_weight=0., biome_penalty={})
        self.corr.forbid = np.zeros((self.corr.m, self.corr.m), dtype=bool)
        self.edge = dict(id='shore-approach', a='west', b='east',
                         cls='spur', pri=1, grade_max=.18)
        nodes = {
            name: dict(xz=point, patch=self.field.id, island='fixture')
            for name, point in [('west', (-100., 0.)), ('east', (100., 0.))]}
        self.points, _ = N.routemod.resample(
            [(-100., 0.), (-80., 10.), (80., 10.), (100., 0.)], N.STATION_M)
        self.enterContext(patch.object(N.specmod, 'NODES', nodes))
        self.enterContext(patch.object(N, '_PAD_CLAIMS', []))
        self.enterContext(patch.object(N, '_NODE_ANCHORS', {}))
        self.enterContext(patch.object(N, '_PLAN_PAD_KEEPOUTS',
                                       lambda: R.PadKeepOut([], [])))
        # Entirely synthetic measured patches: never invoke the native cache
        # producer/scanner, a world, a server, or any saved terrain provider.
        self.enterContext(patch.object(N, '_PLAN_PATCHES', N._generated_patches))
        self.enterContext(patch.object(N, '_GEN_PATCHES', {}))
        self.enterContext(patch.object(N, '_PLAN_KEEPOUTS', lambda zones: []))
        self.enterContext(patch.object(N, '_WET_REMEDY', 'profile'))

    def exercise_ceiling(self, trim, *, tracked=True):
        joint = {}
        original = N.build_segment(
            self.field, self.edge, self.corr, [], census=None, joint=joint,
            fixed_pts=self.points, trim=trim)
        arc = joint['s']
        # A legitimate lifted design profile: <=3 m fill, <=.15 grade,
        # returning to the existing endpoints. This models the real input to
        # a water re-remedy after another profile solve has lifted a road.
        raised = 32. + 3. * np.minimum(
            np.minimum(arc / 20., (arc[-1] - arc) / 20.), 1.)
        original['profile_y'] = [round(float(y), 3) for y in raised]
        joint['y0'] = np.asarray(original['profile_y'])
        self.assertTrue(np.all(joint['y0'] >= joint['lo']))
        self.assertTrue(np.all(joint['y0'] <= joint['hi']))
        self.assertTrue(np.all(np.abs(np.diff(joint['y0'])) <=
                               original['grade_limit'] * np.diff(arc) + .002))
        judged = N.writer_water(original, self.field)
        self.assertGreater(judged['steps'], 0)
        self.assertGreater(judged['water_fill_clips'], 0)
        self.assertTrue(judged['_drops'])
        caps = {k: original['profile_y'][k] - drop
                for k, drop in judged['_drops'].items()}
        charged_points = {k: tuple(original['nodes'][k]) for k in caps}
        original_points = np.asarray(original['nodes']).copy()
        original_mask = self.corr.forbid.copy()

        def raw(**kw):
            state = {}
            record = N.build_segment(
                self.field, self.edge, self.corr, [], census=None,
                joint=state, **kw)
            return record, state

        build = N.keep_trim(raw, trim)
        if tracked:
            build = N.tracked_build(build, None)
        result, _ = N.water_route(
            self.field, self.corr, original, joint, build=build,
            refusal=lambda record: N.plan_refusal(
                record, [], N.SEED, fld=self.field, coverage=False))
        final = N.writer_water(result, self.field)
        self.assertEqual(final['steps'], 0)
        self.assertEqual(final['drained'], 0)
        self.assertEqual(final['verdict'], 'CLEAR')
        # Clearing a different, inland road is not a ceiling repair. Every
        # station keeps its actual world coordinate and every previously
        # charged station receives its cap at that same coordinate.
        np.testing.assert_array_equal(result['nodes'], original_points)
        if 'water_trim' in original:
            self.assertEqual(result['water_trim'], original['water_trim'])
        for k, point in charged_points.items():
            self.assertEqual(tuple(result['nodes'][k]), point)
            self.assertLessEqual(result['profile_y'][k], caps[k] + .002)
        self.assertTrue(any(result['profile_y'][k] <
                            original['profile_y'][k] - 1. for k in caps))
        np.testing.assert_array_equal(self.corr.forbid, original_mask)

        # Freezing one profile remedy must not freeze the next real search,
        # nor forget a prior terminal trim. Compare actual searched geometry,
        # not callback arguments or bookkeeping keys.
        future, _ = build()
        expected = N.build_segment(
            self.field, self.edge, self.corr, [], census=None, trim=trim)
        np.testing.assert_array_equal(future['nodes'], expected['nodes'])
        self.assertLess(max(z for _, z in future['nodes']), 1.)
        return result, original_points

    def test_untracked_probe_preserves_trimmed_ceiling_geometry(self):
        self.exercise_ceiling({'a': 3, 'b': 3}, tracked=False)

    def test_fixed_profile_preserves_released_abutment_bounds(self):
        nodes = {
            'ar-far': dict(xz=(0., 0.), patch='ab', island='ab'),
            'ar-abutment': dict(xz=(80., 0.), patch='ab', island='ab',
                                kind='abutment', level_y=30.6)}
        self.enterContext(patch.object(N.specmod, 'NODES', nodes))
        self.enterContext(patch.object(N, '_ABUTMENT_TRIMMED_END_MODE', 'released'))
        field = N._ar_field(40.)
        corridor = N._ww_corridor(field)
        edge = dict(id='AR', a='ar-far', b='ar-abutment', cls='trunk', pri=3)
        line = [[float(x), 0.] for x in range(0, 82, 2)]
        original = N.build_segment(
            field, edge, corridor, [], fixed_pts=line, trim={'b': 16},
            census=None, joint={})
        self.assertEqual(original['profile_y'][-1], 40.)

        def raw(**kw):
            joint = {}
            result = N.build_segment(
                field, edge, corridor, [], census=None, joint=joint, **kw)
            return result, joint

        build = N.tracked_build(N.keep_trim(raw, {'b': 16}), None)
        result, joint = build(fixed_pts=original['nodes'], ceiling={0: 40.},
                              trim_applied=True)
        np.testing.assert_array_equal(result['nodes'], original['nodes'])
        self.assertEqual(result['profile_y'][-1], 40.)
        self.assertGreaterEqual(joint['lo'][-1], 33.)
        self.assertEqual(
            [(row['node'], row['stations_trimmed'])
             for row in result['abutments_released']],
            [('ar-abutment', 16)])

    def test_ceiling_repairs_original_station_geometry(self):
        self.exercise_ceiling(None)

    def test_ceiling_does_not_retrim_or_detach_already_trimmed_geometry(self):
        result, points = self.exercise_ceiling({'a': 3, 'b': 3})
        np.testing.assert_array_equal(result['nodes'][0], points[0])
        np.testing.assert_array_equal(result['nodes'][-1], points[-1])
        # These are the original bent-road attachments after exactly one
        # trim; the nominal straight-road trim lands on different ground.
        self.assertGreater(result['nodes'][0][1], 2.)
        self.assertGreater(result['nodes'][-1][1], 2.)


if __name__ == '__main__':
    unittest.main()
