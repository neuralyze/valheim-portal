"""Native searched candidates retain grade-propagated owner conflicts as costs."""
import copy
import unittest

import numpy as np

import network as N
import test_protected_feedback as P


class OwnedGradeGuidanceTests(unittest.TestCase):
    def fixture(self, *, movable=True, chord=False, trace=True):
        corr, _, entry, goal = P.ProtectedFeedbackTests()._numeric_hairpin(outer_cost=1000.)
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        corr.forbid |= (xx == -72.) & (abs(zz) < 30.)
        prefix = np.array([[-80., -48.], [-78., -48.], [-76., -48.], [-74., -48.]])
        if chord:
            # A real searched U and native straightening chord whose center
            # is well clear of every coarse path vertex, at unchanged radius.
            allowed = ((xx == -72.) & (abs(zz) <= 72.))
            allowed |= (abs(zz) == 48.) & (xx >= -72.) & (xx <= -48.)
            allowed |= (xx == -48.) & (abs(zz) >= 18.) & (abs(zz) <= 48.)
            allowed |= (abs(zz) == 18.) & (abs(xx) <= 48.)
            allowed |= (xx == 48.) & (abs(zz) <= 18.)
            allowed |= (abs(zz) == 72.) & (abs(xx) <= 72.)
            allowed |= (abs(xx) == 72.) & (abs(zz) <= 72.)
            corr.forbid = ~allowed | ((xx == -72.) & (abs(zz) < 30.))
            prefix = np.array([[-48., -34.], [-48., -32.], [-48., -30.], [-48., -28.]])
            entry = (-48., -26.)
        before = {k: getattr(corr, k).copy() for k in
                  ('H', 'Hmin', 'Hmax', 'water', 'forbid', 'unread')}
        rejected = []
        grade, reach, lattice = N.writer_batter()

        def build(guidance):
            local = copy.copy(corr)
            local.guidance = guidance
            raw = local.search(entry, goal)
            tail, _ = N.routemod.resample(raw, 2.)
            points = np.vstack((prefix, tail))
            arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            terrain = np.full(len(points), 60.)
            lower, upper = terrain - 7., terrain + 7.
            held = np.zeros(len(points), dtype=bool)
            lower[:4] = upper[:4] = 54.
            held[:4] = True
            banks = [['a', 'declared-bank', 54., 0, 3]]
            at = np.flatnonzero((points[:, 0] >= -64.) & (points[:, 0] <= -56.)
                                & (points[:, 1] == -48.))
            crossings = ([dict(crossing_id='new-crossing', stations=[int(at[0]), int(at[-1])])]
                         if movable and len(at) else [])
            if chord:
                first = np.flatnonzero((points[:, 0] >= -48.) & (points[:, 1] == -18.))
                last = np.flatnonzero((points[:, 0] >= -48.) & (points[:, 1] == 18.))
                crossings = ([dict(crossing_id='new-crossing',
                                   stations=[int(first[0]), int(last[-1])])]
                             if len(first) and len(last) and first[0] < last[-1]
                             else [])
            free, windows = N.crossing_windows(points, arcs, crossings)
            before_chord = points.copy()
            if trace:
                for crossing, ia, ib, _, _ in windows:
                    crossing['search_path_before_chord'] = before_chord[ia:ib + 1].tolist()
            if chord:
                points, windows = N.straighten_windows(
                    points, arcs, windows, banks, preserve_bank_geometry=True)
                points = np.asarray(points, dtype=np.float64)
                arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            detected, free_windows = [], []
            for crossing, ia, ib, wa, wb in windows:
                lower[wa:wb + 1] = upper[wa:wb + 1] = 58.
                held[wa:wb + 1] = True
                detected.append(dict(first=wa, last=wb, lower_y=58., upper_y=58.,
                                     preferred_y=58., published_y=58.,
                                     crossing_id=crossing['crossing_id']))
                free_windows.append(dict(crossing_id=crossing['crossing_id'],
                                         first_free=ia, last_free=ib,
                                         first_level=wa, last_level=wb))
            if not movable:
                # Two fixed owners already contradict each other before any
                # movable tail. No alignment of that tail can release either.
                lower[4:8] = upper[4:8] = 58.
                held[4:8] = True
                banks.append(['b', 'other-declared-bank', 58., 4, 7])
            fit = N.routemod.fit_ladder(
                terrain, arcs, lower, upper, free, [.25], pts=points, width_m=6.,
                grade=grade, batter_m=reach, lattice_m=lattice, held=held)
            heights = np.asarray(fit['y'])
            bad = np.flatnonzero(held & ((heights < lower - 1e-9) |
                                        (heights > upper + 1e-9)))
            record = dict(id='owned-grade', cls='trunk', **{'from': 'a', 'to': 'b'},
                          width_m=6., length_m=float(arcs[-1]), nodes=points.tolist(),
                          profile_y=heights.tolist(), is_bridge=free.tolist(),
                          crossings=crossings, self_adjacency=fit['joined'],
                          _numeric_raw_search=raw,
                          profile_constraints=dict(
                              verdict='REFUSED' if len(bad) else 'CLEAR',
                              bindings=[dict(station=int(k), xz=points[k].tolist(),
                                             lower_y=float(lower[k]), upper_y=float(upper[k]),
                                             profile_y=float(heights[k])) for k in bad]))
            joint = dict(s=arcs, t=terrain, lo=lower, hi=upper, held=held, free=free,
                         rung=fit['rung'], terrain_feasible=fit['terrain_feasible'],
                         spans=fit['fit']['infeasible_spans'], cand=fit['cand'],
                         native_basis=dict(held_abutment_inputs=banks,
                                           held_detected_bank_inputs=detected,
                                           free_crossing_windows=free_windows))
            if len(bad):
                rejected.append((record, joint, fit))
                cause = N._OwnProfileRefusal('actual native propagated owner refusal',
                                             candidate=(record, joint))
                raise ValueError('actual searched attachment refused') from cause
            return record, joint
        return corr, build, prefix, before, rejected

    def test_prechord_trace_prices_real_search_when_final_chord_center_misses(self):
        corr, build, prefix, before, rejected = self.fixture(chord=True)
        with self.assertRaises(ValueError):
            build(None)
        initial, _, fit = rejected[0]
        self.assertFalse(fit['terrain_feasible'])
        hint = N._owned_profile_hint(initial)
        self.assertEqual(hint['search_path_before_chord'],
                         initial['crossings'][0]['search_path_before_chord'])
        radius = N.routemod.footprint_half_m(initial['width_m']) + np.sqrt(2.) * corr.cell_m / 2.
        hint.update(radius_m=radius, cost_per_m=1.)
        raw = initial['_numeric_raw_search']
        physical_center = N.adjacency_mask(corr, [(*hint['xz'], radius)])
        self.assertFalse(any(physical_center[corr.cell_of(*point)] for point in raw[1:]))
        costs = N.adjacency_guidance(corr, [hint])
        self.assertTrue(any(costs[corr.cell_of(*point)] > 0. for point in raw[1:]),
                        'measured pre-chord path must price the real searched path')
        np.testing.assert_array_equal(costs[costs > 0.], 1.)
        result, joint = N.adjacency_reroute(corr, build)
        self.assertIsNone(N.profile_constraint_rejection(result))
        self.assertIsNone(N.self_adjacency_rejection(result))
        self.assertEqual(result['nodes'][-1], initial['nodes'][-1])
        np.testing.assert_array_equal(np.asarray(result['nodes'])[:4], prefix)
        np.testing.assert_array_equal(result['profile_y'][:4], 54.)
        y = np.asarray(result['profile_y'])
        self.assertTrue(np.all(y >= joint['lo'] - 1e-9))
        self.assertTrue(np.all(y <= joint['hi'] + 1e-9))
        self.assertTrue(np.all(abs(np.diff(y)) <= joint['rung'] * np.diff(joint['s']) + 1e-9))
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)

    def test_missing_or_malformed_prechord_trace_keeps_original_bank_refusal(self):
        corr, build, prefix, before, rejected = self.fixture(chord=True, trace=False)
        with self.assertRaises(ValueError):
            build(None)
        record, joint, _ = rejected[0]
        self.assertIsNone(N._owned_profile_hint(record))
        variants = [None, [], [[0., 0.]], [[float('nan'), 0.], [0., 0.]],
                    [[0., 0.], (0., 0.)],
                    [[0., 0., 0.], [1., 1., 1.]], 'not a sampled path']
        for path in variants:
            with self.subTest(path=path):
                candidate = copy.deepcopy(record)
                candidate['crossings'][0]['search_path_before_chord'] = path
                error = N._OwnProfileRefusal('original owned bank refusal',
                                             candidate=(candidate, joint))
                self.assertIsNone(N._owned_profile_hint(candidate))
                self.assertIsNone(N._refused_adjacency_candidate(error))
                self.assertEqual(candidate['profile_constraints']['verdict'], 'REFUSED')
                with self.assertRaisesRegex(ValueError, 'pre-chord search path'):
                    N.adjacency_guidance(corr, [dict(
                        crossing_id='new-crossing', xz=record['nodes'][0],
                        search_path_before_chord=path, radius_m=5., cost_per_m=1.)])
        with self.assertRaisesRegex(ValueError, 'actual searched attachment refused'):
            N.adjacency_reroute(corr, build)
        np.testing.assert_array_equal(np.asarray(record['nodes'])[:4], prefix)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)

    def test_actual_search_reprices_new_crossing_not_fixed_bank(self):
        corr, build, prefix, before, rejected = self.fixture()
        result, joint = N.adjacency_reroute(corr, build)
        self.assertTrue(rejected)
        initial, original, fitted = rejected[0]
        self.assertFalse(fitted['terrain_feasible'])
        self.assertTrue(fitted['fit']['infeasible_spans'])
        self.assertTrue(all(b['lower_y'] == b['upper_y']
                            for b in initial['profile_constraints']['bindings']))
        hint = N._owned_profile_hint(initial)
        self.assertEqual(hint['crossing_id'], 'new-crossing')
        self.assertGreater(hint['grade_conflict']['shortfall_m'], 0.)
        self.assertNotIn(hint['xz'], initial['nodes'][:4])
        self.assertIsNone(N.profile_constraint_rejection(result))
        self.assertIsNone(N.self_adjacency_rejection(result))
        self.assertEqual(result['nodes'][-1], initial['nodes'][-1])
        np.testing.assert_array_equal(np.asarray(result['nodes'])[:4], prefix)
        np.testing.assert_array_equal(result['profile_y'][:4], 54.)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)
        self.assertTrue(all(row['kind'] == 'owned_profile_grade'
                            for row in result['adjacency_reroute']['hints']))

    def test_missing_native_owner_inventory_never_guesses_from_midpoint(self):
        _, build, _, _, rejected = self.fixture()
        with self.assertRaises(ValueError):
            build(None)
        record, joint, _ = rejected[0]
        record = copy.deepcopy(record)
        record['profile_constraints'].pop('grade_propagated', None)
        joint = {k: v for k, v in joint.items() if k != 'native_basis'}
        error = N._OwnProfileRefusal('original owner refusal', candidate=(record, joint))
        self.assertIsNone(N._owned_profile_hint(record))
        self.assertIsNone(N._refused_adjacency_candidate(error))
        self.assertEqual(record['profile_constraints']['verdict'], 'REFUSED')

    def test_no_movable_owner_preserves_actual_native_refusal(self):
        corr, build, prefix, before, rejected = self.fixture(movable=False)
        with self.assertRaisesRegex(ValueError, 'actual searched attachment refused') as cm:
            N.adjacency_reroute(corr, build)
        self.assertIsInstance(cm.exception.__cause__, N._OwnProfileRefusal)
        self.assertTrue(rejected)
        rec, joint, fit = rejected[0]
        self.assertFalse(fit['terrain_feasible'])
        self.assertTrue(fit['fit']['infeasible_spans'])
        self.assertIsNone(N._owned_profile_hint(rec))
        self.assertIsNone(N._refused_adjacency_candidate(cm.exception))
        np.testing.assert_array_equal(np.asarray(rec['nodes'])[:4], prefix)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)


if __name__ == '__main__':
    unittest.main()
