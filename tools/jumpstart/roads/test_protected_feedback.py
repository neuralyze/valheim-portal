"""Protected-ground retry geometry, independent of any world's producer."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import network as net


def _corridor():
    n, step = 97, 2.0
    field = net.fieldmod.Field(
        'protected-feedback', 0., 0., n * step / 2., step, n,
        np.full((n, n), 60., dtype=np.float32),
        np.ones((n, n), dtype=np.uint8))
    corr = net.routemod.Corridor(
        field, cell_m=step, grade_max=.25,
        grade_weight=0., roughness_weight=0., biome_penalty={})
    corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
    corr.unread = np.zeros_like(corr.forbid)
    return corr


class ProtectedFeedbackTests(unittest.TestCase):
    def test_cut_and_fill_faces_require_the_same_batter_clearance(self):
        corr = _corridor()
        physical = {k: getattr(corr, k).copy() for k in
                    ('H', 'Hmin', 'Hmax', 'water', 'forbid', 'unread')}
        # Both faces differ by 7.5931 m: one is a road cut below protected
        # ground, the other a road filled above it. The signed fill-only
        # formula gave the cut zero reach and never moved its alignment.
        kwargs = dict(half_m=3., grade=.7812856265, reach_m=21.)
        cut = net.keepout_standoff_forbid(
            corr, [(0., 0., 9., 63.2185, 70.8116)], **kwargs)
        fill = net.keepout_standoff_forbid(
            corr, [(0., 0., 9., 70.8116, 63.2185)], **kwargs)
        np.testing.assert_array_equal(cut, fill)
        self.assertTrue(cut[corr.cell_of(20., 0.)])
        self.assertFalse(cut[corr.cell_of(28., 0.)])
        for name, values in physical.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)

    def test_cut_feedback_moves_real_search_without_moving_its_endpoints(self):
        corr = _corridor()
        original = corr.search((-72., 18.), (72., 18.))
        overlay = net.keepout_standoff_forbid(
            corr, [(0., 0., 9., 63.2185, 70.8116)],
            half_m=3., grade=.7812856265, reach_m=21.)
        guided = copy.copy(corr)
        guided.forbid = corr.forbid | overlay
        alternate = guided.search(original[0], original[-1])
        self.assertNotEqual(alternate, original)
        self.assertEqual((alternate[0], alternate[-1]),
                         (original[0], original[-1]))
        for point in alternate:
            self.assertFalse(overlay[corr.cell_of(*point)])
        self.assertFalse(corr.forbid.any())

    def test_protected_reach_retains_cut_extreme_when_other_faces_are_filled(self):
        # Aggregating only maximum road height and minimum protected ground
        # erases the deep cut at the opposite face of the SAME disc.
        faces = [(-10., -30., 54., 68.), (-10., 30., 66., 65.)]
        seeds = net.keepout_standoff_seeds(faces, [(0., 0., 10.)])
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0][:3], (0., 0., 10.))
        self.assertEqual(abs(seeds[0][3] - seeds[0][4]), 14.)
        self.assertEqual(net.keepout_standoff_seeds(
            list(reversed(faces)), [(0., 0., 10.)]), seeds)

    def test_observed_face_pairs_leave_the_measured_26m_bypass_available(self):
        corr = _corridor()
        faces = [(-10., -20., 54., 60.), (-10., 20., 66., 72.)]
        seeds = net.keepout_standoff_seeds(faces, [(0., 0., 9.)])
        self.assertEqual(abs(seeds[0][3] - seeds[0][4]), 6.)
        self.assertIn(seeds[0][3:], [(54., 60.), (66., 72.)])
        self.assertEqual(net.keepout_standoff_seeds(
            list(reversed(faces)), [(0., 0., 9.)]), seeds)
        overlay = net.keepout_standoff_forbid(
            corr, seeds, half_m=4., grade=.7812856265, reach_m=21.)
        self.assertFalse(overlay[corr.cell_of(0., 26.)])
        # The only measured bypass lies 26 m from the protected disc. Combining
        # unrelated heights into (54,72) wrongly forbids this entire crossing.
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        corr.forbid = ~((zz == 26.) & (abs(xx) <= 72.))
        original = corr.search((-72., 26.), (72., 26.))
        guided = copy.copy(corr)
        guided.forbid = corr.forbid | overlay
        self.assertEqual(guided.search(original[0], original[-1]), original)


    def test_water_alignment_reuses_self_adjacency_remedy_on_numeric_hairpin(self):
        self._check_water_hairpin(near_terminal=False)

    def test_end_near_hairpin_cannot_be_detached_by_adjacency_retry(self):
        self._check_water_hairpin(near_terminal=True)

    def _check_water_hairpin(self, *, near_terminal):
        corr = _corridor()
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        # Direct alignment, a shorter hairpin bypass, and a longer open bypass.
        allowed = ((xx == -72.) & (abs(zz) <= 72.))
        allowed |= ((abs(zz) == 48.) & (xx >= -72.) & (xx <= -48.))
        allowed |= ((xx == -48.) & (abs(zz) >= 6.) & (abs(zz) <= 48.))
        allowed |= ((abs(zz) == 6.) & (abs(xx) <= 48.))
        allowed |= ((xx == 48.) & (abs(zz) <= 6.))
        allowed |= ((abs(zz) == 72.) & (abs(xx) <= 72.))
        allowed |= ((xx == 72.) & (abs(zz) <= 72.))
        if near_terminal:
            # A short diagonal approach reaches the terminal beside the later
            # hairpin foot. Its protected face forces the initial water retry
            # onto the hairpin without blocking that terminal.
            diagonal_x = -72. + (zz + 48.) * 24. / 56.
            allowed |= ((zz >= -48.) & (zz <= 8.)
                        & (abs(xx - diagonal_x) <= 2.))
        corr.forbid = ~allowed
        physical_forbid = corr.forbid.copy()
        a = (-72., -48.)
        b = (-48., 8.) if near_terminal else (-72., 48.)
        grade, reach, lattice = net.writer_batter()

        def build(*, extra_forbid=None, extra_guidance=None,
                  trim=None, ceiling=None):
            if trim or ceiling:
                raise ValueError('rigid-datum fixture cannot trim or lower its pins')
            local = copy.copy(corr)
            if extra_forbid is not None:
                local.forbid = corr.forbid | extra_forbid
            local.guidance = extra_guidance
            raw = local.search(a, b)
            pts, s = net.routemod.resample(raw, 2.)
            s = np.asarray(s, dtype=float)
            # Rigid engineering datums within the unmodified +/-8 m clamp.
            # They leave the long bypass feasible but PROVE the nearby hairpin
            # branches cannot join at any allowed grade, rather than faking a
            # self-adjacency verdict in the builder.
            target = 54. + 12. * s / s[-1]
            free = np.zeros(len(pts), dtype=bool)
            fitted = net.routemod.fit_ladder(
                np.full(len(pts), 60.), s, target, target, free, [.25],
                pts=pts, width_m=6., grade=grade,
                batter_m=reach, lattice_m=lattice)
            rec = dict(id='protected-hairpin', **{'from': 'a', 'to': 'b'},
                       width_m=6., length_m=float(s[-1]),
                       nodes=[list(p) for p in pts],
                       profile_y=list(fitted['y']), is_bridge=list(free),
                       crossings=[], self_adjacency=fitted['joined'])
            return rec, {}

        original, joint = build()
        original['profile_y'] = [54.] * len(original['nodes'])
        saved = (net._PLAN_PATCHES, net._PLAN_KEEPOUTS,
                 net._PLAN_PAD_KEEPOUTS)
        try:
            net._PLAN_PATCHES = net._PLAN_PAD_KEEPOUTS = None
            disc = (-71., -20., 6.) if near_terminal else (-83., 0., 6.)
            net._PLAN_KEEPOUTS = lambda _zones: [disc]
            before = net.writer_water(original, corr.fld)
            self.assertGreater(before['keepout_steps'], 0)
            overlay = net.keepout_standoff_forbid(
                corr, before['_keepout'], half_m=4.,
                grade=grade, reach_m=reach)
            hairpin, _ = build(extra_forbid=overlay)
            self.assertEqual(hairpin['self_adjacency']['verdict'], 'REFUSED')
            self.assertGreater(
                hairpin['self_adjacency']['worst_before']['unabsorbable_m'], 0.)
            if near_terminal:
                moved, _ = net.adjacency_reroute(
                    corr, lambda adj: build(extra_forbid=overlay | adj),
                    initial=(hairpin, {}))
                self.assertIsNone(net.self_adjacency_rejection(moved))
                self.assertGreater(np.linalg.norm(
                    np.asarray(moved['nodes'][-1]) - np.asarray(b)), 4.)
            out, _ = net.water_route(
                corr.fld, corr, original, joint, build=build,
                refusal=lambda rec: ('self_adjacency' if
                    net.self_adjacency_rejection(rec) is not None else None))
            if near_terminal:
                self.assertEqual(out['writer_water']['verdict'], 'REFUSED')
                np.testing.assert_array_equal(out['nodes'], original['nodes'])
            else:
                self.assertEqual(out['writer_water']['verdict'], 'CLEAR')
                self.assertIsNone(net.self_adjacency_rejection(out))
            self.assertEqual((tuple(out['nodes'][0]), tuple(out['nodes'][-1])),
                             (a, b))
            if not near_terminal:
                for point in out['nodes']:
                    self.assertFalse(overlay[corr.cell_of(*point)])
            self.assertLessEqual(max(abs(y - 60.) for y in out['profile_y']), 8.)
        finally:
            (net._PLAN_PATCHES, net._PLAN_KEEPOUTS,
             net._PLAN_PAD_KEEPOUTS) = saved
        np.testing.assert_array_equal(corr.forbid, physical_forbid)
        self.assertIsNone(corr.guidance)



if __name__ == '__main__':
    unittest.main()
