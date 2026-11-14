"""A rejected new level window must not strand an otherwise legal road search."""
from pathlib import Path
import copy
import sys
import unittest

import numpy as np

import network as N
from roads import test_protected_feedback as P


class OwnedProfileGuidanceTests(unittest.TestCase):
    def test_conflicting_level_window_does_not_end_or_stall_the_legal_route_search(self):
        corr, _, a, b = P.ProtectedFeedbackTests()._numeric_hairpin(outer_cost=1000.)
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        corr.forbid |= (xx == -72.) & (abs(zz) < 30.)
        # A third measured path is cheaper than the legal outer bypass, but
        # its new rigid level window conflicts with the source's fixed 6 m run.
        west = ((xx == -92.) & (abs(zz) <= 48.))
        west |= ((abs(zz) == 48.) & (xx >= -92.) & (xx <= -72.))
        corr.forbid[west] = False
        corr.soft = np.where(west, 150., corr.soft)
        physical = {name: getattr(corr, name).copy() for name in
                    ('H', 'Hmin', 'Hmax', 'water', 'forbid', 'unread')}
        grade, reach, lattice = N.writer_batter()
        rejected = []

        def build(guidance):
            local = copy.copy(corr)
            local.guidance = guidance
            raw = local.search(a, b)
            points, arcs = N.routemod.resample(raw, 2.)
            points = np.asarray(points, dtype=float)
            arcs = np.asarray(arcs, dtype=float)
            lower = 54. + 12. * arcs / arcs[-1]
            lower[arcs <= N.BANK_LEVEL_M] = 54.
            upper = lower.copy()
            # Two independent engineering owners meet at a true interval
            # contradiction. No refusal or self-adjacency result is mocked.
            new_level = ((points[:, 0] <= -78.) & (points[:, 0] >= -90.)
                         & (abs(points[:, 1] + 48.) < .1))
            lower[new_level] = np.maximum(lower[new_level], 58.)
            upper[new_level] = np.minimum(upper[new_level], 58.)
            free = np.zeros(len(points), dtype=bool)
            fit = N.routemod.fit_ladder(
                np.full(len(points), 60.), arcs, lower, upper, free, [.25],
                pts=points, width_m=6., grade=grade,
                batter_m=reach, lattice_m=lattice,
                held=np.ones(len(points), dtype=bool))
            heights = np.asarray(fit['y'])
            bad = np.flatnonzero((heights < lower - 1e-9)
                                 | (heights > upper + 1e-9))
            bindings = [dict(station=int(k), xz=points[k].tolist(),
                             lower_y=float(lower[k]), upper_y=float(upper[k]),
                             profile_y=float(heights[k])) for k in bad]
            record = dict(id='owned-window-routes', **{'from': 'a', 'to': 'b'},
                          width_m=6., length_m=float(arcs[-1]),
                          nodes=points.tolist(), profile_y=heights.tolist(),
                          is_bridge=free.tolist(), crossings=[],
                          self_adjacency=fit['joined'],
                          profile_constraints=dict(
                              verdict='REFUSED' if len(bad) else 'CLEAR',
                              held_stations=len(points), bindings=bindings))
            joint = dict(lo=lower, hi=upper, s=arcs)
            if len(bad):
                rejected.append(record)
                error = N._OwnProfileRefusal('actual overlapping owned level bounds')
                error.candidate = record, joint
                raise error
            return record, joint

        initial, joint = build(None)
        self.assertIsNotNone(N.self_adjacency_rejection(initial))
        result, _ = N.adjacency_reroute(corr, build, initial=(initial, joint))
        self.assertTrue(rejected)
        self.assertIsNone(N.profile_constraint_rejection(result))
        self.assertIsNone(N.self_adjacency_rejection(result))
        self.assertEqual((tuple(result['nodes'][0]), tuple(result['nodes'][-1])), (a, b))
        np.testing.assert_array_equal(np.asarray(result['profile_y'])[0], 54.)
        np.testing.assert_array_equal(np.asarray(result['profile_y'])[-1], 66.)
        self.assertLessEqual(max(abs(y - 60.) for y in result['profile_y']), 7.)
        for name, before in physical.items():
            np.testing.assert_array_equal(getattr(corr, name), before)
        self.assertIsNone(corr.guidance)


if __name__ == '__main__':
    unittest.main()
