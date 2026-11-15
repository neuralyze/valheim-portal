"""Real numeric contact/wet-bay composition, including shipping float32 closure."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import test_water_bank_contacts as bank_fixture
from test_span_remedy_feedback import _physical_snapshot

N = bank_fixture.N
R = bank_fixture.R


class SoftWaterStandoffTests(unittest.TestCase):
    def fixture(self):
        field, _corr, edge, pads = bank_fixture.WaterBankContactsTests.fixture(self)

        def generated(x, z):
            x, z = np.asarray(x, dtype=float), np.asarray(z, dtype=float)
            land = 31.1 + .075 * np.maximum(-z, 0.)
            # A connected, narrow bay beside the incoming road. Its near end
            # meets an optional connector, not the immutable six-metre landing.
            a, b = np.array([-8., 1.]), np.array([5., -12.])
            run = b - a
            t = np.clip(((x - a[0]) * run[0] + (z - a[1]) * run[1])
                        / np.dot(run, run), 0., 1.)
            distance = np.hypot(x - (a[0] + t * run[0]),
                                z - (a[1] + t * run[1]))
            bay = (land - 29.8) * np.exp(-(distance / 1.5) ** 4)
            connector = (1.1002 * np.exp(-((x + 5.) ** 2 + (z - 1.) ** 2) / .125)
                         + 1.1131 * np.exp(-((x + 6.) ** 2 + (z - 1.) ** 2) / .125))
            return land - bay - connector

        axis = field.x0 + np.arange(field.n) * field.step
        x, z = np.meshgrid(axis, axis)
        field.h[:] = generated(x, z).astype(np.float32)
        corr = N.routemod.Corridor(
            field, cell_m=4., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        corr.soft = np.full((corr.m, corr.m), .125)
        # The inland endpoint is beyond the wet face's terminal-trim reach.
        N.specmod.NODES['inland']['xz'] = (30., -38.)
        self.enterContext(bank_fixture.patch.object(
            N, '_PLAN_PATCHES', lambda fld, zones: {
                f'z_{zx}_{zz}': R._flat_patch(zx, zz, generated)
                for zx, zz in zones}))
        return field, corr, edge, pads

    def build(self, field, corr, edge, **kwargs):
        joint = {}
        record = N.build_segment(
            field, edge, corr, [], census=None, joint=joint, **kwargs)
        bank_fixture.profile_refit.require_native_profile(record)
        return record, joint

    def assert_decoded_clear(self, record, field, pads):
        judged, water, arrivals, walk, clamped = (
            bank_fixture.WaterBankContactsTests.decode(self, record, field, pads))
        self.assertEqual(judged['rim']['caused'], 0)
        self.assertEqual(judged['rim']['rim_samples_unreadable'], 0)
        self.assertEqual(water['drained_samples'], 0)
        self.assertEqual(arrivals, [])
        self.assertEqual(walk['verdict'], 'WALKABLE')
        self.assertLessEqual(walk['max_gradient_8m'], N.routemod.GRADE_CEILING)
        self.assertEqual(clamped, 0)

    def test_contact_advancement_composes_with_soft_connected_water_standoff(self):
        field, corr, edge, pads = self.fixture()
        physical = _physical_snapshot(corr)
        with redirect_stdout(StringIO()):
            initial, joint = self.build(field, corr, edge)
            first_water = N.writer_water(initial, field)
            first_contact = N._read_bank_contacts(initial)
            alternate, _ = self.build(
                field, corr, edge, bank_contacts_excluded=[first_contact])
            alternate_water = N.writer_water(alternate, field)
        self.assertEqual(first_water['verdict'], 'REFUSED')
        self.assertTrue(first_water['_seeds'])
        self.assertEqual(alternate_water['verdict'], 'REFUSED')
        self.assertGreater(alternate_water['steps'], 0)
        self.assertNotEqual(alternate['search_contacts']['b'], initial['search_contacts']['b'])
        # The alternate's remaining face is beyond the owned level run, but
        # whole-body connectivity makes a HARD retry cover that valid run.
        root = N.specmod.NODES['shore']['xz']
        entry = (root[0], root[1] - N.BANK_LEVEL_M)
        self.assertTrue(all(np.linalg.norm(np.asarray((x, z)) - entry)
                            > N.routemod.footprint_half_m(edge['width_m'])
                            for x, z, _height in alternate_water['_seeds']))
        mask = N.routemod.water_standoff_forbid(
            corr, alternate_water['_seeds'],
            half_m=N.routemod.footprint_half_m(edge['width_m']),
            grade=N.writer_batter()[0], reach_m=N.writer_batter()[1])
        self.assertTrue(N._bank_path_allowed(corr, [root, entry], None))
        self.assertFalse(N._bank_path_allowed(corr, [root, entry], mask))

        # Establish that a real, finite-cost native candidate exists. No bank
        # provider is replaced: the ordinary native exclusion API selects it.
        guidance = mask.astype(np.float64)
        self.assertTrue(np.isfinite(guidance).all())
        self.assertEqual(float(guidance.max()), 1.)
        with redirect_stdout(StringIO()):
            witness, _ = self.build(
                field, corr, edge, bank_contacts_excluded=[first_contact],
                extra_guidance=guidance)
        self.assertNotEqual(witness['nodes'], alternate['nodes'])
        self.assertEqual(N.writer_water(witness, field)['verdict'], 'CLEAR')
        self.assert_decoded_clear(witness, field, pads)

        def build(**kwargs):
            return self.build(field, corr, edge, **kwargs)

        with redirect_stdout(StringIO()):
            result, _ = N.water_route(
                field, corr, initial, joint, build=N.tracked_build(build, None),
                refusal=lambda r: N.plan_refusal(r, [], N.SEED, fld=field))
        self.assertEqual(result['writer_water']['verdict'], 'CLEAR')
        self.assertNotEqual(result['search_contacts']['b'], initial['search_contacts']['b'])
        self.assertIsNone(N.arrival_geometry.fixed_landing_rejection(
            reversed(result['nodes']), root, 0., N.BANK_LEVEL_M))
        self.assertEqual(result['nodes'][-4:], initial['nodes'][-4:])
        # A changed free approach may improve its measured cut/fill. The
        # owned datum, run, bearing, width and endpoint must remain identical.
        self.assertEqual(
            [{k: v for k, v in a.items() if k != 'worst_cut_fill_m'}
             for a in result['abutments']],
            [{k: v for k, v in a.items() if k != 'worst_cut_fill_m'}
             for a in initial['abutments']])
        self.assertTrue(all(a['within_clamp'] for a in result['abutments']))
        self.assertTrue(all(a['worst_cut_fill_m'] <= N.routemod.CUT_FILL_MAX
                            for a in result['abutments']))
        self.assertEqual(result['width_m'], edge['width_m'])
        bank_fixture.profile_refit.require_native_profile(result)
        self.assert_decoded_clear(result, field, pads)
        for name, values in physical.items():
            np.testing.assert_array_equal(getattr(corr, name), values)
        self.assertIsNone(corr.guidance)

    def test_soft_water_cost_never_unmasks_poi_pad_or_unread_landing(self):
        for owner in ('poi', 'pad', 'unread'):
            with self.subTest(owner=owner):
                field, corr, edge, _pads = self.fixture()
                with redirect_stdout(StringIO()):
                    initial, joint = self.build(field, corr, edge)
                if owner == 'poi':
                    poi = N.poimod.Poi('protected', 1., 5., 31.1, 1., True, 1)
                    hard, _soft, _touched = N.poimod.masks(
                        [poi], corr.x0, corr.z0, corr.cell_m, corr.m,
                        edge['width_m'] / 2.)
                elif owner == 'pad':
                    hard = N.pad_forbid_mask(
                        [dict(x=1., z=5., hw=1., hd=1., role='installation_pad')],
                        corr.x0, corr.z0, corr.cell_m, corr.m,
                        N.routemod.footprint_half_m(edge['width_m']))
                else:
                    hard = np.zeros_like(corr.forbid)
                    hard[corr.cell_of(1., 4.)] = True
                    corr.unread |= hard
                corr.forbid |= hard
                corr.arrival_forbid = hard.copy()
                self.assertFalse(N._bank_path_allowed(corr, [(1., 8.), (1., 2.)], None))
                physical = _physical_snapshot(corr)
                arrival_forbid = corr.arrival_forbid.copy()

                def build(**kwargs):
                    return self.build(field, corr, edge, **kwargs)

                with redirect_stdout(StringIO()):
                    result, _ = N.water_route(
                        field, corr, initial, joint, build=N.tracked_build(build, None),
                        refusal=lambda r: N.plan_refusal(r, [], N.SEED, fld=field))
                self.assertEqual(result['writer_water']['verdict'], 'REFUSED')
                for name, values in physical.items():
                    np.testing.assert_array_equal(getattr(corr, name), values)
                np.testing.assert_array_equal(corr.arrival_forbid, arrival_forbid)
                self.assertIsNone(corr.guidance)


if __name__ == '__main__':
    unittest.main()
