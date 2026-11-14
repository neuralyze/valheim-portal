"""Route around a new rigid crossing, not the immovable bank it conflicts with."""
import copy
import unittest
from unittest.mock import patch
import numpy as np
import network as N
import test_protected_feedback as P


class CrossingOwnedGuidanceTests(unittest.TestCase):
    def fixture(self, *, close_direct=True):
        corr, _, entry, goal = P.ProtectedFeedbackTests()._numeric_hairpin(outer_cost=1000.)
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        if close_direct:
            corr.forbid |= (xx == -72.) & (abs(zz) < 30.)
        before = {k: getattr(corr, k).copy() for k in
                  ('H', 'Hmin', 'Hmax', 'water', 'forbid', 'unread')}
        prefix = np.array([[-80., -48.], [-78., -48.], [-76., -48.], [-74., -48.]])
        grade, reach, lattice = N.writer_batter()
        rejected = []

        def build(guidance, extra_forbid=None):
            local = copy.copy(corr)
            local.guidance = guidance
            if extra_forbid is not None:
                local.forbid = corr.forbid | extra_forbid
            tail, _ = N.routemod.resample(local.search(entry, goal), 2.)
            points = np.vstack((prefix, tail))
            arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            lower = 54. + 12. * np.maximum(arcs - N.BANK_LEVEL_M, 0.) / (
                arcs[-1] - N.BANK_LEVEL_M)
            upper = lower.copy()
            at_crossing = np.flatnonzero((points[:, 0] >= -70.) & (points[:, 0] <= -56.)
                                         & (points[:, 1] == -48.))
            crossings = ([dict(crossing_id='new-crossing', stations=[int(at_crossing[0]),
                          int(at_crossing[-1])])] if len(at_crossing) else [])
            free, windows = N.crossing_windows(points, arcs, crossings)
            for crossing, ia, ib, wa, wb in windows:
                crossing['search_path_before_chord'] = points[ia:ib + 1].tolist()
                lower[wa:wb+1] = np.maximum(lower[wa:wb+1], 58.)
                upper[wa:wb+1] = np.minimum(upper[wa:wb+1], 58.)
            fitted = N.routemod.fit_ladder(np.full(len(points), 60.), arcs, lower, upper,
                free, [.25], pts=points, width_m=6., grade=grade, batter_m=reach,
                lattice_m=lattice, held=np.ones(len(points), dtype=bool))
            heights = np.asarray(fitted['y'])
            bad = np.flatnonzero((heights < lower - 1e-9) | (heights > upper + 1e-9))
            bindings = [dict(station=int(k), xz=points[k].tolist(), lower_y=float(lower[k]),
                            upper_y=float(upper[k]), profile_y=float(heights[k])) for k in bad]
            record = dict(id='bank-crossing', **{'from':'a','to':'b'}, width_m=6.,
                length_m=float(arcs[-1]), nodes=points.tolist(), profile_y=heights.tolist(),
                is_bridge=free.tolist(), crossings=crossings, self_adjacency=fitted['joined'],
                profile_constraints=dict(verdict='REFUSED' if len(bad) else 'CLEAR', bindings=bindings))
            joint = dict(lo=lower, hi=upper, s=arcs)
            if len(bad):
                rejected.append(record)
                raise N._OwnProfileRefusal('new crossing conflicts with fixed source bank',
                                           candidate=(record, joint))
            return record, joint
        return corr, build, prefix, before, rejected

    def test_new_crossing_cannot_strand_the_fixed_source_bank(self):
        corr, build, prefix, before, rejected = self.fixture()

        result, joint = N.adjacency_reroute(corr, build)
        self.assertTrue(rejected)
        self.assertIsNone(N.profile_constraint_rejection(result))
        self.assertIsNone(N.self_adjacency_rejection(result))
        np.testing.assert_array_equal(np.asarray(result['nodes'])[:len(prefix)], prefix)
        np.testing.assert_array_equal(np.asarray(result['profile_y'])[joint['s'] <= N.BANK_LEVEL_M], 54.)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)

    def test_writer_feedback_can_route_around_a_new_owned_level_conflict(self):
        corr, build, prefix, before, rejected = self.fixture(close_direct=False)
        original, joint = build(None)

        def rebuild(*, extra_forbid=None, extra_guidance=None, **fixed):
            if any(value is not None and value is not False for value in fixed.values()):
                raise ValueError('fixture exercises genuine alignment searches only')
            return build(extra_guidance, extra_forbid)

        def refusal(record):
            if N.profile_constraint_rejection(record) is not None:
                return 'profile_constraints'
            if N.self_adjacency_rejection(record) is not None:
                return 'self_adjacency'
            return None

        with patch.object(N, '_PLAN_PATCHES', None), \
             patch.object(N, '_PLAN_PAD_KEEPOUTS', None), \
             patch.object(N, '_PLAN_KEEPOUTS', lambda _zones: [(-83., -20., 6.)]):
            initial_water = N.writer_water(original, corr.fld)
            self.assertGreater(initial_water['keepout_steps'], 0)
            result, state = N.water_route(
                corr.fld, corr, original, joint, build=rebuild, refusal=refusal)
        self.assertTrue(rejected)
        self.assertEqual(result['writer_water']['verdict'], 'CLEAR')
        self.assertEqual(result['writer_water']['keepout_steps'], 0)
        self.assertIsNone(N.profile_constraint_rejection(result))
        self.assertIsNone(N.self_adjacency_rejection(result))
        np.testing.assert_array_equal(np.asarray(result['nodes'])[:len(prefix)], prefix)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)


if __name__ == '__main__':
    unittest.main()
