"""Actual native profile constraints remain binding during measured repairs."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import profile_contract as contract
import profile_refit
import ribbon as R
import route


def native_segment(points=None, *, profile=None, held=(), bridge=(), bounds=None,
                   published_readings=()):
    """A complete constructed native problem, solved by the shipping solver."""
    points = np.asarray(points if points is not None else
                        [[float(x), 0.] for x in range(0, 102, 2)])
    arcs = np.r_[0., np.cumsum(np.hypot(*np.diff(points, axis=0).T))]
    y0 = np.asarray(profile if profile is not None else np.full(len(points), 40.))
    ground = y0.copy()
    lo = (np.maximum(ground - route.CUT_FILL_MAX, 30.6) if bounds is None
          else np.asarray(bounds[0]).copy())
    hi = (ground + route.CUT_FILL_MAX if bounds is None
          else np.asarray(bounds[1]).copy())
    held_mask = np.zeros(len(points), dtype=bool)
    for k in held:
        held_mask[k] = True
        lo[k] = hi[k] = y0[k]
    free = np.zeros(len(points), dtype=bool)
    free[list(bridge)] = True
    cand = route.self_adjacency_lattice(
        points, free, 2., grade=R.BATTER_GRADE, batter_m=R.BATTER_MAX_M,
        lattice_m=1., dy_cap=.25, terminal_caps=(True, True))
    seg = dict(id='native-numeric', cls='spur', width_m=2.,
               grade_limit=.25, nodes=points.tolist(), is_bridge=free.tolist(),
               terminal_caps=[True, True], terminal_owners=[None, None],
               profile_y=y0.tolist())
    bridge_stations = np.flatnonzero(free)
    windows = ([dict(crossing_id='constructed-deck',
                     first_free=int(bridge_stations[0]),
                     last_free=int(bridge_stations[-1]))]
               if len(bridge_stations) else [])
    joint = dict(s=arcs, t=ground, lo=lo, hi=hi, tw_lo=ground, tw_hi=ground,
                 free=free, held=held_mask, ladder=[.25], design_rung=.25,
                 cand=cand, terminal_owners=[None, None], arrival_readings=[], spans=[],
                 native_basis=dict(geometry_arcs=arcs.tolist(), water_level=30.,
                     producer='constructed exact native problem',
                     measurement_field='numeric test ground',
                     geometry_preparation={'source': 'authored exact points'},
                     held_abutment_inputs=[['constructed-bank', int(k), int(k)]
                                           for k in np.flatnonzero(held_mask)],
                     held_detected_bank_inputs=[],
                     free_crossing_windows=windows, walked_water_ceiling_inputs=[],
                     road_freeboard_m=route.ROAD_FREEBOARD_M,
                     deck_clearance_m=route.DECK_CLEARANCE_M,
                     cut_fill_max_m=route.CUT_FILL_MAX, clamp_m=route.CLAMP_M,
                     self_join_margin_m=route.SELF_ADJACENCY_JOIN_MARGIN_M))
    # This baseline really enters the exact native solver. No mocked solver,
    # synthesized CLEAR report, or inherited count supplies the constraint set.
    provisional = contract.build_native_profile_model(seg, joint,
                                     published_readings=published_readings)
    seg['native_profile_model'] = provisional
    model = contract.read_native_profile_model(seg)
    matrix, lower, upper = contract.native_profile_readings(model)
    solved = route._exact_reading_profile(y0, lo, hi, [(0, len(arcs), .25 * arcs)],
                                           matrix, lower, upper)
    if solved is None:
        raise AssertionError('constructed native baseline must be feasible')
    seg['profile_y'] = solved.tolist()
    contract.bind_native_profile_model(seg, model)
    return seg


def claim(s, h, slack=0.):
    return dict(s=float(s), h=float(h), slack=float(slack))


class NativeProfileRefitTests(unittest.TestCase):
    def test_total_grade_not_shift_grade_keeps_30_6_held_datum(self):
        arcs = np.arange(0., 102., 2.)
        y = 30.6 + .25 * np.maximum(arcs - 6., 0.)
        seg = native_segment(profile=y, held=range(4))
        original = copy.deepcopy(seg)
        result = profile_refit.refit_native_profile(seg, [claim(50., y[25] + 1.)])
        # The old shift hat at .08 was legal in its own coordinates, while
        # TOTAL grade exceeded .25. The held datum has already spent all reach.
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CLAIMS_INFEASIBLE')
        self.assertEqual(seg, original)
        self.assertEqual(result['profile'][:4], [30.6] * 4)

    def test_arrival_unpin_cannot_release_native_held_bank_station(self):
        seg = native_segment(profile=np.full(51, 30.6), held=range(4))
        result = profile_refit.refit_native_profile(seg, [dict(claim(6., 31.6), unpin=3)])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['profile'][3], 30.6)

    def test_published_deck_cannot_move_inside_original_free_interval(self):
        seg = native_segment(bridge=range(24, 28))
        model = contract.read_native_profile_model(seg)
        self.assertLess(model['inputs']['lo'][25], 40.)
        self.assertGreater(model['inputs']['hi'][25], 40.)
        result = profile_refit.refit_native_profile(seg, [claim(50., 41.)])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CLAIMS_INFEASIBLE')
        self.assertEqual(result['profile'][25], 40.)

    def test_original_terrain_upper_bound_cannot_be_spent_again(self):
        lo, hi = np.full(51, 33.), np.full(51, 47.)
        hi[25] = 40.2
        seg = native_segment(bounds=(lo, hi))
        result = profile_refit.refit_native_profile(seg, [claim(50., 40.5)])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CLAIMS_INFEASIBLE')

    def test_published_interpolated_joint_datum_is_not_a_movable_centroid(self):
        shared = dict(columns=[24, 25], weights=[.25, .75], lower_y=40.,
                      upper_y=40., kind='published_joint', owner='other-road')
        seg = native_segment(published_readings=[shared])
        result = profile_refit.refit_native_profile(seg, [claim(49.5, 41.)])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CLAIMS_INFEASIBLE')

    def test_native_freeboard_lower_bound_cannot_be_lowered(self):
        seg = native_segment(profile=np.full(51, 30.6))
        result = profile_refit.refit_native_profile(seg, [claim(50., 30.4)])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CLAIMS_INFEASIBLE')

    def test_actual_native_self_lattice_rejects_otherwise_grade_feasible_readings(self):
        points = [[0., float(z)] for z in range(0, 42, 2)]
        points += [[2., 40.], [4., 40.], [6., 40.]]
        points += [[6., float(z)] for z in range(38, -2, -2)]
        seg = native_segment(points)
        model = contract.read_native_profile_model(seg)
        cand = model['self_lattice']
        edges = cand['edges']
        use = np.flatnonzero((cand['allow_e'] < 2.)
                            & (np.abs(edges['s1'] - edges['s2']) > 24.))
        self.assertGreater(len(use), 0)
        k = int(use[0])
        claims = [claim(edges['s1'][k], 38.), claim(edges['s2'][k], 42.)]
        # Prove it is the native self restriction, not a short chain or bound.
        feet = route._feet(model['inputs']['s'],
                           np.asarray([c['s'] for c in claims]))
        matrix = route._reading_matrix(
            np.column_stack(feet[:2]),
            np.column_stack((1. - feet[2], feet[2])), len(points))
        target = np.asarray([38., 42.])
        relaxed = route._exact_reading_profile(seg['profile_y'],
            model['inputs']['lo'], model['inputs']['hi'],
            [(0, len(points), .25 * model['inputs']['s'])], matrix, target, target)
        self.assertIsNotNone(relaxed)
        self.assertTrue(route.self_adjacency_violations(relaxed, cand))
        result = profile_refit.refit_native_profile(seg, claims)
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CLAIMS_INFEASIBLE')

    def test_incomplete_self_inventory_is_not_a_complete_native_context(self):
        points = [[0., float(z)] for z in range(0, 42, 2)]
        points += [[2., 40.], [4., 40.], [6., 40.]]
        points += [[6., float(z)] for z in range(38, -2, -2)]
        seg = native_segment(points)
        incomplete = seg['native_profile_model']
        cand = incomplete['self_lattice']
        self.assertGreater(len(cand['edges']['s1']), 0)
        for key in ('near1', 'near2', 'dsum', 'allow_e'):
            cand[key] = cand[key][:0]
        cand['edges'] = {key: values[:0] for key, values in cand['edges'].items()}
        # Simulate a defective export after the complete constructor succeeds.
        # A consistent hash must not certify the omitted native restrictions.
        incomplete['sha256'] = contract._digest(
            {key: value for key, value in incomplete.items() if key != 'sha256'})
        result = profile_refit.refit_native_profile(seg, [claim(30., 41.)])
        self.assertFalse(result['feasible'])
        self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CONTEXT_REFUSED')

    def test_missing_model_and_each_exact_binding_change_refuse(self):
        original = native_segment()
        variants = []
        missing = copy.deepcopy(original)
        missing.pop('native_profile_model')
        variants.append(missing)
        for field, value in [('width_m', 4.), ('grade_limit', .3), ('cls', 'trunk')]:
            altered = copy.deepcopy(original)
            altered[field] = value
            variants.append(altered)
        changed = copy.deepcopy(original)
        changed['nodes'][25][1] += .01
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed['profile_y'][25] += .01
        variants.append(changed)
        for seg in variants:
            with self.subTest(binding=seg.get('native_profile_model', {}).get('schema')):
                before = copy.deepcopy(seg)
                result = profile_refit.refit_native_profile(seg, [claim(50., 41.)])
                self.assertFalse(result['feasible'])
                self.assertEqual(result['refusal_kind'], 'NATIVE_PROFILE_CONTEXT_REFUSED')
                self.assertEqual(seg, before)
                with self.assertRaises(ValueError):
                    profile_refit.require_native_profile(seg)

    def test_positive_exact_interpolated_repair_preserves_geometry_bounds_and_bank(self):
        y = np.full(51, 40.)
        y[:4] = 39.
        y[4:6] = [39.5, 40.]
        seg = native_segment(profile=y, held=range(4))
        before = copy.deepcopy(seg)
        measured = claim(49.5, 41.)
        result = profile_refit.refit_native_profile(seg, [measured])
        self.assertTrue(result['feasible'], result)
        self.assertGreater(result['nodes_regraded'], 0)
        self.assertEqual(seg, before)  # Proposal API is read-only.
        candidate = copy.deepcopy(seg)
        profile_refit.accept_native_profile(candidate, result)
        model = profile_refit.require_native_profile(candidate)
        self.assertEqual(candidate['nodes'], before['nodes'])
        np.testing.assert_array_equal(candidate['profile_y'][:4], [39.] * 4)
        self.assertAlmostEqual(float(np.interp(49.5, model['inputs']['s'],
                                              candidate['profile_y'])), 41., places=9)
        self.assertLessEqual(float(np.max(abs(np.diff(candidate['profile_y']))
                                  / np.diff(model['inputs']['s']))), .25 + 1e-9)
        self.assertFalse(route.self_adjacency_violations(
                             candidate['profile_y'], model['self_lattice']))

    def test_actual_compiler_feedback_repairs_frozen_rim_inside_native_model(self):
        seg, rasterise, judge = R._pf_fixture(100.)
        original_nodes = copy.deepcopy(seg['nodes'])
        comps, stats, walk = rasterise(seg)
        before = judge(comps, stats)['rim']
        self.assertGreater(before['caused'], 0)
        comps, stats, walk, report = R.profile_feedback(
            seg, comps, stats, walk, rasterise, judge,
            limit_m=R.BATTER_GRADE, edge_m=4.)
        after = judge(comps, stats)['rim']
        self.assertEqual(after['caused'], 0, report)
        self.assertEqual(seg['nodes'], original_nodes)
        self.assertEqual(seg['profile_y'][0], R._PF_Y)
        self.assertEqual(seg['profile_y'][-1], R._PF_Y)
        self.assertEqual(stats['over_clamp'], 0)
        profile_refit.require_native_profile(seg)

    def test_missing_context_keeps_actual_caused_rim_refused_without_mutation(self):
        seg, rasterise, judge = R._pf_fixture(100.)
        seg.pop('native_profile_model')
        original = copy.deepcopy(seg)
        comps, stats, walk = rasterise(seg)
        before = judge(comps, stats)['rim']
        comps, stats, walk, report = R.profile_feedback(
            seg, comps, stats, walk, rasterise, judge,
            limit_m=R.BATTER_GRADE, edge_m=4.)
        after = judge(comps, stats)['rim']
        self.assertGreater(after['caused'], 0)
        self.assertEqual(after['caused'], before['caused'])
        self.assertFalse(report['rounds'][0]['feasible'])
        self.assertEqual(report['rounds'][0]['refusal_kind'],
                         'NATIVE_PROFILE_CONTEXT_REFUSED')
        self.assertEqual(seg['profile_y'], original['profile_y'])
        self.assertEqual(seg['nodes'], original['nodes'])
        self.assertNotIn('native_profile_model', seg)
        self.assertEqual(seg['native_profile_status']['verdict'], 'UNPROVEN')
        self.assertEqual(seg['profile_constraints']['verdict'], 'REFUSED')
        self.assertEqual(seg['self_adjacency']['verdict'], 'UNPROVEN')


if __name__ == '__main__':
    unittest.main()
