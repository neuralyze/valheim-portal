"""Numeric native closure regressions; no world, ledger or source-wiring assertions."""
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "terraform"), str(HERE.parent)]
import ribbon as R
import junction as J
from rim_coupling import continue_component

# Exact offline W7 neighbourhood, translated so the contradicted cell is (0,0).
# Columns: cell, genuine generated Heightmap height, emitted height, native claim.
W7 = [((-2, 0), 41.43546676635742, 38.78742042564188, (4.956288934273308, 37.259)),
 ((-1, -3), 39.281558990478516, 36.19937125301344, (4.5, 34.63680000000001)),
 ((-1, -2), 39.69807434082031, 36.980656879520154, (5.5, 34.63680000000001)),
 ((-1, -1), 40.277496337890625, 39.89119046863168, (6.369050164663491, 37.259)),
 ((-1, 0), 40.677825927734375, 39.40299398171299, (5.737863280010837, 37.263940753699984)),
 ((-1, 1), 40.87375259399414, 39.042868240851, (5.153377422668201, 37.36046541207625)),
 ((0, -3), 38.510353088378906, 36.079371253013434, (4.5, 34.5168)),
 ((0, -2), 38.92292785644531, 36.86065687952015, (5.5, 34.5168)),
 ((0, -1), 39.61670684814453, 37.641942506026865, (6.5, 34.5168)),
 ((0, 0), 40.11080551147461, 40.106462657262405, (6.54926717608631, 37.333471227954085)),
 ((0, 1), 40.413490295410156, 39.746336916400416, (5.9647813187436745, 37.42999588633035)),
 ((0, 2), 40.833343505859375, 39.33728120024945, (5.438648729234064, 37.432)),
 ((1, -2), 38.361236572265625, 36.74065687952015, (5.5, 34.396800000000006)),
 ((1, -1), 39.09151077270508, 37.52194250602687, (6.5, 34.396800000000006)),
 ((1, 0), 39.65049743652344, 38.30322813253358, (7.360671072161651, 37.403001702208186)),
 ((1, 1), 39.96563720703125, 39.0845137590403, (6.799919117165965, 37.432))]


class CoupledRimTests(unittest.TestCase):
    def setup_surface(self, ground, written, claims, *, road=(), before=None,
                      fixed=None, refuse=None, foreign=None, max_rounds=None):
        comp = R.tcdata.Compiler(0, 0)
        comps = {(0, 0): comp}
        patches = {"z_0_0": R._flat_patch(0, 0, ground)}
        road = set(road)
        batter = {}
        for c, h in written.items():
            gx, gy = R.tcdata.vertex_mask_index(*comp.centre, *c)
            g = R.appliedmod.generated_at(patches, *c)
            comp.set_height(gx, gy, h - g)
            if c not in road:
                batter[c] = (0, 0, gx, gy, g)
        stats, records = {}, []
        before = before or ground
        fixed = fixed or (lambda *c: None)
        refuse = refuse or (lambda *a: None)
        foreign = foreign or (lambda *c: None)

        def after(x, z):
            f = fixed(x, z)
            if f is not None:
                return f
            got = R.delta_at(comps, x, z)
            return ground(x, z) + got

        def judge():
            taken = {c: after(*c) for c in written.keys() | batter.keys()
                     if abs(after(*c) - before(*c)) > R.appliedmod.AUTHOR_TOLERANCE_M}
            return J.rim_transfer(taken, before, height_after=after, top=10000)

        def close():
            options = {} if max_rounds is None else {"max_rounds": max_rounds}
            R.close_rim(comps, patches, batter, road, claims, stats,
                        J.SLIDE_PER_M, before_at=before, refuse=refuse,
                        foreign=foreign, edge_m=3., grade=R.BATTER_GRADE,
                        fixed_at=fixed, refeather=lambda: None,
                        record=lambda c, cause, h: records.append((c, cause, h)),
                        **options)
        return after, judge, close, stats, records, comps

    def w7(self, **options):
        generated = {c: g for c, g, h, claim in W7}
        ground = lambda x, z: generated.get((x, z), 60.)
        written = {c: h for c, g, h, claim in W7}
        claims = {c: claim for c, g, h, claim in W7}
        return self.setup_surface(ground, written, claims, **options)

    def test_conflicting_movable_neighbours_close_as_a_component(self):
        after, judge, close, stats, records, comps = self.w7()
        original = judge()
        self.assertGreater(original['caused'], 0)
        self.assertTrue(any(r['rim_xz'] == [0, 0] for r in original['caused_rows']))
        hi = after(0, -1) + J.SLIDE_PER_M
        lo = after(0, 1) - J.SLIDE_PER_M
        self.assertGreater(lo, hi)  # Actual numerical sequential contradiction.
        close()
        self.assertEqual(judge()['caused'], 0)
        self.assertLess(after(0, 0), 39.)
        self.assertLess(after(0, 1), 39.746336916400416)
        # Include codec precision, rather than only the in-memory assignment.
        for zone, comp in list(comps.items()):
            parsed = R.tcdata.parse(comp.blob())
            decoded = R.tcdata.Compiler(*zone)
            for i, (level, smooth) in parsed['heights'].items():
                gy, gx = divmod(i, R.tcdata.PITCH)
                decoded.set_height(gx, gy, level, smooth)
            comps[zone] = decoded
        self.assertEqual(judge()['caused'], 0)

    def test_real_pins_are_not_algorithmic_unclosable_cells(self):
        pins = {(0, -1): 37.641942506026865, (0, 1): 39.746336916400416}
        for mode in ('road', 'pad', 'placed', 'poi', 'foreign', 'foreign_band'):
            with self.subTest(mode=mode):
                options = {}
                if mode == 'road':
                    options['road'] = pins
                elif mode == 'pad':
                    options['fixed'] = lambda x, z: pins.get((x, z))
                elif mode in ('placed', 'poi'):
                    options['refuse'] = lambda x, z, g, d, mode=mode: mode if (x, z) in pins else None
                else:
                    def foreign(x, z, mode=mode):
                        h = pins.get((x, z))
                        if h is None:
                            return None
                        return ({'paved': True} if mode == 'foreign' else
                                {'paved': False, 'lo': h, 'hi': h, 'surface': h})
                    options['foreign'] = foreign
                after, judge, close, stats, records, comps = self.w7(**options)
                old = {c: after(*c) for c in pins}
                close()
                self.assertEqual({c: after(*c) for c in pins}, old)
                self.assertGreater(judge()['caused'], 0)

    def single_cell(self, *, fixed=None, refuse=None, generated=40., before=40., claim=(4., 40.)):
        ground = lambda x, z: generated if (x, z) == (0, 0) else before
        return self.setup_surface(
            ground, {(-1, 0): before - 1.5, (0, 0): before + .004},
            {(0, 0): claim}, road={(-1, 0)}, before=lambda *c: before,
            fixed=fixed, refuse=refuse, max_rounds=0)

    def test_diagonal_corner_outside_component_is_checked(self):
        for pad_height, should_move in ((40., True), (41., False)):
            with self.subTest(pad_height=pad_height):
                fixed = lambda x, z: pad_height if (x, z) == (1, 1) else None
                after, judge, close, stats, records, comps = self.single_cell(fixed=fixed)
                original = after(0, 0)
                close()
                if should_move:
                    self.assertLess(after(0, 0), original - .5)
                    affected = [r for r in judge()['caused_rows']
                                if r['rim_xz'] == [1, 1] and r['taken_xz'] == [0, 0]]
                    self.assertEqual(affected, [])
                else:
                    self.assertEqual(after(0, 0), original)
                    self.assertEqual(after(1, 1), 41.)

    def test_delta_sensitive_native_refusal_cannot_commit(self):
        def refusal(x, z, generated, delta):
            return 'placed' if (x, z) == (0, 0) and delta < 0. else None
        after, judge, close, stats, records, comps = self.single_cell(refuse=refusal)
        original = after(0, 0)
        close()
        self.assertEqual(after(0, 0), original)
        self.assertTrue(any(c == (0, 0) and cause == 'placed' for c, cause, h in records))

    def test_own_band_and_apply_clamp_are_binding(self):
        for options in ({'claim': (3.1, 45.)}, {'generated': 48.}):
            with self.subTest(options=options):
                after, judge, close, stats, records, comps = self.single_cell(**options)
                original = after(0, 0)
                close()
                self.assertEqual(after(0, 0), original)
                self.assertGreater(abs(after(-1, 0) - after(0, 0)), J.SLIDE_PER_M)

    def test_submerged_bank_cannot_be_cut_for_a_closure(self):
        after, judge, close, stats, records, comps = self.single_cell(
            generated=29.5, before=29.5, claim=(4., 29.5), refuse=lambda x, z, g, d: R.batter_water_gate(g, d)
        )
        original = after(0, 0)
        close()
        self.assertEqual(after(0, 0), original)

    def test_existing_pre_step_resolution_is_unchanged(self):
        results = R.selftest_rim_before_resolution(verbose=False)
        self.assertEqual(results['current_closure']['verdict'], 'CLEAR')
        self.assertEqual(results['genuinely_preexisting']['verdict'], 'CLEAR')
        self.assertEqual(results['placed']['verdict'], 'REFUSED')

    def test_component_budget_is_bounded_and_deterministic(self):
        heights = {(0, 0): 40., (-1, 0): 38., (1, 0): 42.}
        def height(c):
            return heights.get(c, 40.)
        band = lambda c: (30., 50.) if c in heights else None
        options = dict(height=height, band=band, binds=lambda a, b: a[1] == b[1],
                       limit_m=J.SLIDE_PER_M, validate=lambda assignment: True)
        first = continue_component((0, 0), max_cells=1, **options)
        self.assertEqual(first['assignment'], {})
        self.assertEqual(first['status'], 'bounded')
        solved = continue_component((0, 0), max_cells=3, **options)
        self.assertEqual(solved, continue_component((0, 0), max_cells=3, **options))
        self.assertEqual(solved['status'], 'clear')
        for c, y in solved['assignment'].items():
            for dx in (-1, 1):
                n = (c[0] + dx, c[1])
                self.assertLessEqual(abs(y - solved['assignment'].get(n, height(n))),
                                     J.SLIDE_PER_M + 1e-8)


if __name__ == '__main__':
    unittest.main()
