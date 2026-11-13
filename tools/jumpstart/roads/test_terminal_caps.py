"""Explicit bank boundaries match bridge continuations, without extra ground nodes."""
import copy
import json
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ribbon as R
import junction as J
import clear as C


class TerminalCapsTests(unittest.TestCase):
    @staticmethod
    def geometry(points, varying=False, duplicates=False):
        nodes = np.asarray(points, dtype=np.float64)
        arc = np.asarray(R.node_arcs(nodes, np.zeros(len(nodes), dtype=bool)))
        prof = 40.0 + (0.25 * arc if varying else np.zeros(len(nodes)))
        if duplicates:
            nodes = np.concatenate((nodes[:1], nodes, nodes[-1:]))
            prof = np.concatenate((prof[:1], prof, prof[-1:]))
        return nodes, prof, np.zeros(len(nodes), dtype=bool)

    @staticmethod
    def continuation(nodes, prof, caps):
        points, levels = nodes.copy(), prof.copy()
        bridge = np.zeros(len(nodes), dtype=bool)
        offset = 0.0
        if not caps[0]:
            k = next(k for k in range(1, len(nodes))
                     if np.linalg.norm(nodes[k] - nodes[0]) > 1e-6)
            direction = (nodes[k] - nodes[0]) / np.linalg.norm(nodes[k] - nodes[0])
            marker = nodes[0] - 8.0 * direction
            offset = float(np.linalg.norm(nodes[0] - marker))
            points = np.concatenate(([marker], points))
            levels = np.concatenate(([prof[0]], levels))
            bridge = np.concatenate(([True], bridge))
        if not caps[1]:
            k = next(k for k in range(len(nodes) - 2, -1, -1)
                     if np.linalg.norm(nodes[k] - nodes[-1]) > 1e-6)
            direction = (nodes[-1] - nodes[k]) / np.linalg.norm(nodes[-1] - nodes[k])
            points = np.concatenate((points, [nodes[-1] + 8.0 * direction]))
            levels = np.concatenate((levels, [prof[-1]]))
            bridge = np.concatenate((bridge, [True]))
        return points, levels, bridge, offset

    def test_projection_matches_bridge_encoding(self):
        X, Z = np.meshgrid(np.arange(-5.0, 18.0), np.arange(-5.0, 10.0))
        saw_runner_up = saw_floor = False
        for shape in ([[0., 0.], [6., 0.], [12., 0.]],
                      [[0., 0.], [12., 0.], [12., 4.], [0., 4.]]):
            for varying in (False, True):
                for duplicates in (False, True):
                    nodes, prof, br = self.geometry(shape, varying, duplicates)
                    for caps in ((False, True), (True, False), (False, False)):
                        with self.subTest(shape=shape, varying=varying,
                                          duplicates=duplicates, caps=caps):
                            bn, bp, bb, offset = self.continuation(nodes, prof, caps)
                            actual = R.lateral_and_y_grid(
                                nodes, prof, br, X, Z, 24.0, edge_m=2.0,
                                terminal_caps=caps)
                            expected = R.lateral_and_y_grid(
                                bn, bp, bb, X, Z, 24.0, edge_m=2.0)
                            for i in (0, 1, 3, 4, 5, 6, 7, 8):
                                np.testing.assert_allclose(actual[i], expected[i],
                                                           atol=1e-12, rtol=0)
                            claimed = np.isfinite(actual[0])
                            np.testing.assert_allclose(actual[2][claimed] + offset,
                                                       expected[2][claimed], atol=1e-12)
                            saw_runner_up |= bool(np.isfinite(actual[6]).any())
                            saw_floor |= bool(np.isfinite(actual[8]).any())
                            arc = R.node_arcs(nodes, br)
                            barc = R.node_arcs(bn, bb)
                            for x, z in zip(X.ravel()[::7], Z.ravel()[::7]):
                                scalar = R.lateral_and_y(nodes, prof, br, x, z,
                                                         terminal_caps=caps)
                                reference = R.lateral_and_y(bn, bp, bb, x, z)
                                self.assertEqual(scalar is None, reference is None)
                                foot = R.foot_station(nodes, prof, br, arc, x, z,
                                                      terminal_caps=caps)
                                bfoot = R.foot_station(bn, bp, bb, barc, x, z)
                                self.assertEqual(foot is None, bfoot is None)
                                if scalar is not None:
                                    np.testing.assert_allclose(scalar, reference)
                                    np.testing.assert_allclose(foot[:2], bfoot[:2])
                                    self.assertAlmostEqual(foot[2] + offset, bfoot[2])
                            self.assertEqual(R.bank_ends(nodes, prof, br, terminal_caps=caps),
                                             R.bank_ends(bn, bp, bb))
        self.assertTrue(saw_runner_up, 'fixture must exercise the losing claimant')
        self.assertTrue(saw_floor, 'fixture must exercise a fill floor')

    def test_default_and_none_preserve_existing_bridge_geometry(self):
        nodes, prof, br = self.geometry([[0., 0.], [8., 0.], [8., 4.]], True, True)
        nodes, prof, br, _ = self.continuation(nodes, prof, (False, True))
        X, Z = np.meshgrid(np.arange(-5., 14.), np.arange(-5., 10.))
        expected = R.lateral_and_y_grid(nodes, prof, br, X, Z, 24.0)
        for caps in (None, (True, True)):
            actual = R.lateral_and_y_grid(nodes, prof, br, X, Z, 24.0,
                                          terminal_caps=caps)
            for a, b in zip(actual, expected):
                np.testing.assert_array_equal(a, b)
        ordinary = self.geometry([[0., 0.], [8., 0.]])
        self.assertEqual(R.lateral_and_y(*ordinary, -2., 0.), (2., 40.))
        self.assertIsNone(R.lateral_and_y(*ordinary, -2., 0.,
                                         terminal_caps=(False, True)))
        self.assertEqual(R.lateral_and_y(*ordinary, 0., 2.,
                                        terminal_caps=(False, False)), (2., 40.))

    @staticmethod
    def segment(nodes, prof, br, caps=None):
        return dict(id='bank', nodes=nodes.tolist(), profile_y=prof.tolist(),
                    is_bridge=br.tolist(), width_m=2.0, length_m=24.0,
                    terminal_caps=caps, grade_limit=0.25)

    def test_stamp_owned_raster_matches_real_continuation(self):
        for varying in (False, True):
            for caps in ((False, True), (True, False), (False, False)):
                with self.subTest(varying=varying, caps=caps):
                    nodes, prof, br = self.geometry([[0., 0.], [6., 0.], [12., 0.]],
                                                     varying, True)
                    bn, bp, bb, _ = self.continuation(nodes, prof, caps)
                    ground = lambda x, z: 39.0
                    zones = R.segment_zones(nodes.tolist(), br.tolist(), 2.0)
                    patches = {f'z_{x}_{z}': R._flat_patch(x, z, ground) for x, z in zones}
                    outputs = []
                    for seg in (self.segment(nodes, prof, br, caps),
                                self.segment(bn, bp, bb)):
                        comps, stats = R.stamp(seg, patches, zones, R.PadKeepOut([], []))
                        self.assertEqual(stats['over_clamp'], 0)
                        self.assertLessEqual(stats['max_fill_m'], R.tcdata.CLAMP_M)
                        written = set(map(tuple, stats['written']))
                        outputs.append((written, {p: R.delta_at(comps, *p) for p in written}))
                    self.assertEqual(outputs[0][0], outputs[1][0])
                    self.assertTrue(outputs[0][0])
                    for p, value in outputs[0][1].items():
                        self.assertAlmostEqual(value, outputs[1][1][p], places=6)

    def test_lossless_ledger_and_profile_keep_projection_boundary(self):
        nodes, prof, br = self.geometry([[0., 0.], [6., 0.], [12., 0.]], True)
        seg = self.segment(nodes, prof, br, [False, True])
        seg['terminal_owners'] = {'a': {'crossing_id': 'declared', 'node': [0., 0.]}}
        model = R.emitted_road_model(seg, R.BATTER_MAX_M)
        profile = R.emitted_road_profile(seg, R.BATTER_MAX_M)
        record = json.loads(json.dumps(dict(
            op='terrain_write', seq=1, actor='test',
            params=dict(name='road_bank', role='road_segment', profile=profile),
            meta=dict(road_model=model))))
        replay = R.road_models_from_records([record])['road_bank#1']
        arrivals = R.built_arrival_segments([record], 'road_other')
        for retained in (replay, arrivals[0]):
            self.assertIsNone(R.carriageway_plan_y(retained, -1., 0.))
            self.assertAlmostEqual(R.carriageway_plan_y(retained, 1., 0.), 40.25)
            self.assertEqual(retained['terminal_owners'], seg['terminal_owners'])
        self.assertEqual(profile['terminal_caps'], [False, True])
        self.assertIsNone(C.lateral_fn(seg)(-1., 0.))
        rows = [dict(taken_xz=[x, 0.], rim_xz=[x, 1.],
                     rim_height_m=39., step_after_m=1.) for x in (-1., 3.)]
        claims = R.rim_claims(seg, rows, ['outside', 'inside'], R.BATTER_GRADE, 2.)
        self.assertEqual(len(claims), 1)
        self.assertAlmostEqual(claims[0]['s'], 3.)
        self.assertEqual(claims[0]['label'], 'inside')
        seg['terminal_caps'][0] = True
        seg['terminal_owners']['a']['node'][0] = 999.
        self.assertIsNone(R.carriageway_plan_y(model, -1., 0.))
        self.assertEqual(model['terminal_owners']['a']['node'][0], 0.)
        legacy = copy.deepcopy(record)
        for field in ('terminal_caps', 'terminal_owners'):
            legacy['meta']['road_model'].pop(field)
        old_model = R.road_models_from_records([legacy])['road_bank#1']
        self.assertAlmostEqual(R.carriageway_plan_y(old_model, -1., 0.), 40.)

    def test_junction_ground_runs_keep_terminal_boundaries(self):
        nodes, prof, br = self.geometry([[0., 0.], [6., 0.], [12., 0.]], True, True)
        seg = self.segment(nodes, prof, br, (False, False))
        rib = J.ribbons_from_segments({'segments': [seg]}, 'constructed')[0]
        self.assertIsNone(J._foot_near(rib, 1, -1., 0.))
        self.assertIsNone(J._height_at(rib, 13., 0.))
        self.assertAlmostEqual(J._height_at(rib, 3., 1.)[0], 40.75)
        bn, bp, bb, _ = self.continuation(nodes, prof, (False, False))
        other = J.ribbons_from_segments({'segments': [self.segment(bn, bp, bb)]},
                                       'bridge encoding')[0]
        for x in (-1., 0., 3., 12., 13.):
            self.assertEqual(J._height_at(rib, x, 0.), J._height_at(other, x, 0.))

    def test_junction_withheld_caps_preserve_corner_and_returning_branch(self):
        for shape, point, expected_y, expected_distance in (
                ([[0., 0.], [8., 0.], [8., 8.]], (9., -1.), 42., 2. ** 0.5),
                ([[0., 0.], [8., 0.], [8., 4.], [-4., 4.]],
                 (-2., 4.), 45.5, 0.)):
            with self.subTest(shape=shape, point=point):
                nodes, prof, br = self.geometry(shape, True, True)
                seg = self.segment(nodes, prof, br, (False, False))
                rib = J.ribbons_from_segments({'segments': [seg]}, 'constructed')[0]
                reading = J._height_at(rib, *point)
                self.assertIsNotNone(reading)
                self.assertAlmostEqual(reading[0], expected_y)
                self.assertAlmostEqual(reading[1], expected_distance)
                canonical = R.lateral_and_y(nodes, prof, br, *point,
                                             terminal_caps=(False, False))
                np.testing.assert_allclose(reading, canonical[::-1])

    def test_junction_ordinary_caps_keep_legacy_internal_clamps(self):
        nodes, prof, br = self.geometry([[0., 0.], [6., 0.], [12., 0.]], True)
        for caps in (None, (True, True)):
            with self.subTest(caps=caps):
                seg = self.segment(nodes, prof, br, caps)
                rib = J.ribbons_from_segments({'segments': [seg]}, 'constructed')[0]
                self.assertEqual(J._height_at(rib, -1., 0.), (40., 1.))
                self.assertEqual(J._closest_on_run(rib, 1, -1., 0.), (7., 41.5, 6.))


    def test_junction_benches_overlap_even_when_centrelines_miss_bank(self):
        for separation in (1., 1.9999995):
            for varying in (False, True):
                a_nodes = np.asarray([[-separation, z] for z in (-4., -2., 0., 2., 4.)])
                b_nodes = np.asarray([[0., 0.], [6., 0.], [12., 0.]])
                a_profile = np.asarray([40. + (0.125 * (z + 4.) if varying else 0.)
                                        for _, z in a_nodes])
                b_profile = np.asarray([42. + (0.125 * x if varying else 0.)
                                        for x, _ in b_nodes])
                a = self.segment(a_nodes, a_profile, np.zeros(5, dtype=bool))
                b = self.segment(b_nodes, b_profile, np.zeros(3, dtype=bool),
                                 (False, True))
                a.update(id='vertical', width_m=4.)
                b.update(id='bank', width_m=4.)
                for segments in ([a, b], [b, a]):
                    with self.subTest(separation=separation, varying=varying,
                                      order=[s['id'] for s in segments]):
                        ribbons = J.ribbons_from_segments(
                            {'segments': segments}, 'constructed bench overlap')
                        report = J.find_conflicts(ribbons, [])
                        rows = [r for r in report['pairs']
                                if not r['self_overlap'] and r['gap_m'] == 0.]
                        self.assertTrue(any(r['impassable'] for r in rows))
                        by_name = {r['label']: r for r in ribbons}
                        for row in rows:
                            x, z = row['xz']
                            exact = bool(row.get('capped_witness'))
                            tolerance = 1e-12 if exact else 0.005
                            places = 12 if exact else 3
                            self.assertGreaterEqual(x, -1e-9)
                            self.assertLessEqual(x, 2. - separation + tolerance)
                            self.assertLessEqual(abs(z), 2. + tolerance)
                            self.assertEqual(row['gap_m'], 0.)
                            for side in ('a', 'b'):
                                height, distance = J._height_at(by_name[row[side]], x, z)
                                self.assertLessEqual(distance, 2. + tolerance)
                                self.assertAlmostEqual(row[side + '_y'], height, places=places)
                                if exact:
                                    foot = J._foot_near(by_name[row[side]], 0, x, z)
                                    self.assertAlmostEqual(row[side + '_foot_arc'], foot[2],
                                                           places=12)
                            self.assertAlmostEqual(row['spread_m'],
                                                   abs(row['a_y'] - row['b_y']), places=places)

    def test_skinny_capped_overlap_has_an_unrounded_analytic_witness(self):
        separation = 2. - 5e-8
        a_nodes, a_profile, a_br = self.geometry(
            [[-separation, -4.], [-separation, 0.], [-separation, 4.]])
        b_nodes, b_profile, b_br = self.geometry([[0., 0.], [6., 0.], [12., 0.]])
        a = self.segment(a_nodes, a_profile, a_br)
        b = self.segment(b_nodes, b_profile + 2., b_br, (False, True))
        a.update(id='vertical', width_m=4.)
        b.update(id='bank', width_m=4.)
        A, B = J.ribbons_from_segments({'segments': [a, b]}, 'skinny lens')
        witness = J._capped_shared_point(A, 1, B, 0, -separation, 0., (0., 0.))
        self.assertIsNotNone(witness)
        x, z, fa, fb = witness
        self.assertGreaterEqual(x, 0.)
        self.assertLessEqual(x, 2. - separation + 1e-12)
        self.assertLessEqual(abs(z), 2.)
        self.assertLessEqual(fa[1], 2. + 1e-12)
        self.assertLessEqual(fb[1], 2. + 1e-12)


    def test_closer_excluded_bank_cannot_hide_distant_returning_edge(self):
        a_nodes = np.asarray([[-3., -1.], [-3., 1.]])
        b_nodes = np.asarray([[float(x), 0.] for x in range(0, 41, 2)]
                             + [[40., 20.], [-6.5, 20.], [-6.5, -1.]])
        a = self.segment(a_nodes, np.full(2, 40.), np.zeros(2, dtype=bool))
        b = self.segment(b_nodes, np.full(len(b_nodes), 42.),
                         np.zeros(len(b_nodes), dtype=bool), (False, True))
        a.update(id='short', width_m=2.)
        b.update(id='return', width_m=6.)
        for segments in ([a, b], [b, a]):
            with self.subTest(order=[s['id'] for s in segments]):
                ribbons = J.ribbons_from_segments({'segments': segments}, 'long return')
                rows = [r for r in J.find_conflicts(ribbons, [])['pairs']
                        if not r['self_overlap'] and r['gap_m'] == 0.]
                self.assertEqual({r['spread_m'] for r in rows}, {2.})
                self.assertTrue(all(r['impassable'] for r in rows))
                self.assertEqual(len(rows), len({r['a_station'] for r in rows}))
                by_name = {r['label']: r for r in ribbons}
                for row in rows:
                    x, z = row['xz']
                    self.assertGreaterEqual(x, -4. - 1e-9)
                    self.assertLessEqual(x, -3.5 + 1e-9)
                    side = 'a' if row['a'] == 'return' else 'b'
                    self.assertGreater(row[side + '_foot_arc'], 106.5)
                    for participant in ('a', 'b'):
                        height, distance = J._height_at(by_name[row[participant]], x, z)
                        self.assertLessEqual(distance,
                                             by_name[row[participant]]['half_m'] + 1e-9)
                        self.assertEqual(row[participant + '_y'], height)


    def test_capped_near_miss_reads_two_owned_boundaries_with_actual_gap(self):
        for gap in (0.25, J.NEAR_MISS_M, J.NEAR_MISS_M + 1e-4):
            for varying in (False, True):
                separation = 1. + gap
                a_nodes = np.asarray([[-separation, z] for z in (-4., -2., 0., 2., 4.)])
                b_nodes = np.asarray([[0., 0.], [6., 0.], [12., 0.]])
                a_profile = np.asarray([40. + (0.125 * (z + 4.) if varying else 0.)
                                        for _, z in a_nodes])
                b_profile = np.asarray([42. + (0.125 * x if varying else 0.)
                                        for x, _ in b_nodes])
                a = self.segment(a_nodes, a_profile, np.zeros(5, dtype=bool))
                b = self.segment(b_nodes, b_profile, np.zeros(3, dtype=bool),
                                 (False, True))
                a.update(id='vertical', width_m=2.)
                b.update(id='bank', width_m=2.)
                for segments in ([a, b], [b, a]):
                    with self.subTest(gap=gap, varying=varying,
                                      order=[s['id'] for s in segments]):
                        ribbons = J.ribbons_from_segments({'segments': segments}, 'near bank')
                        rows = [r for r in J.find_conflicts(ribbons, [])['pairs']
                                if not r['self_overlap']]
                        if gap > J.NEAR_MISS_M:
                            self.assertEqual(rows, [])
                            continue
                        self.assertAlmostEqual(
                            min((r['gap_m'] for r in rows), default=float('inf')),
                            gap, places=12)
                        if not varying:
                            self.assertTrue(all(r['impassable'] for r in rows))
                        by_name = {r['label']: r for r in ribbons}
                        for row in rows:
                            pa, pb = row['a_reading_xz'], row['b_reading_xz']
                            actual_gap = float(np.linalg.norm(np.asarray(pa) - pb))
                            self.assertAlmostEqual(actual_gap, gap, places=12)
                            self.assertAlmostEqual(row['gap_m'], actual_gap, places=12)
                            self.assertAlmostEqual(
                                row['slide_allow_m'], float(np.tan(np.deg2rad(38.))),
                                places=3)
                            for side, point in (('a', pa), ('b', pb)):
                                rib = by_name[row[side]]
                                foot = J._foot_near(rib, 0, *point)
                                self.assertIsNotNone(foot)
                                self.assertLessEqual(foot[1], rib['half_m'] + 1e-12)
                                self.assertAlmostEqual(row[side + '_y'], foot[0], places=12)
                                self.assertAlmostEqual(row[side + '_foot_arc'], foot[2],
                                                       places=12)
                                if row[side] == 'bank':
                                    self.assertGreaterEqual(point[0], 0.)
                                    self.assertAlmostEqual(foot[2], 0., places=12)
                            self.assertAlmostEqual(row['spread_m'],
                                                   abs(row['a_y'] - row['b_y']), places=12)


    def test_finite_distant_return_foot_cannot_borrow_excluded_fan_gap(self):
        a_nodes = np.asarray([[-3., -1.], [-3., 0.], [-3., 1.]])
        b_nodes = np.asarray([[0., 0.], [6., 0.], [6., 10.],
                              [-10., 10.], [-10., -4.]])
        a = self.segment(a_nodes, np.full(3, 40.), np.zeros(3, dtype=bool))
        b = self.segment(b_nodes, np.full(5, 42.), np.zeros(5, dtype=bool),
                         (False, True))
        a.update(id='short', width_m=2.)
        b.update(id='return', width_m=2.)
        for segments in ([a, b], [b, a]):
            with self.subTest(order=[s['id'] for s in segments]):
                ribbons = J.ribbons_from_segments({'segments': segments}, 'excluded fan')
                returning = next(r for r in ribbons if r['id'] == 'return')
                distant = J._foot_near(returning, 0, -1.5, 0.)
                self.assertIsNotNone(distant)
                self.assertGreater(
                    distant[1], returning['half_m'] + J.NEAR_MISS_M)
                actual = [r for r in J.find_conflicts(ribbons, [])['pairs']
                          if not r['self_overlap']]
                self.assertEqual(actual, [], 'all real footprint gaps exceed the near limit')
                ordinary = [{**s, 'terminal_caps': (True, True)} for s in segments]
                ordinary_ribbons = J.ribbons_from_segments(
                    {'segments': ordinary}, 'ordinary terminal fans')
                legacy = [r for r in J.find_conflicts(ordinary_ribbons, [])['pairs']
                          if not r['self_overlap']]
                self.assertTrue(any(r['impassable'] for r in legacy))


if __name__ == '__main__':
    unittest.main()
