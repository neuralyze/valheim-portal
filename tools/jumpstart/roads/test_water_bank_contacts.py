"""A water-refused optional connector must not freeze the owned six-metre landing."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import network as N
import profile_refit
import ribbon as R
from test_span_remedy_feedback import _physical_snapshot


class _GeneratedBefore:
    def delta_at(self, x, z):
        return 0.

    def sample(self, x, z, by=None):
        return 0., None


class WaterBankContactsTests(unittest.TestCase):
    def fixture(self, wet=True):
        def generated(x, z):
            ground = 31.1 + np.zeros_like(np.asarray(x, dtype=float))
            if wet:
                ground -= 1.1002 * np.exp(-((x + 5.) ** 2 + (z - 1.) ** 2) / .125)
                ground -= 1.1131 * np.exp(-((x + 6.) ** 2 + (z - 1.) ** 2) / .125)
            return ground

        n = 128
        axis = np.arange(n, dtype=float) - n / 2 + .5
        x, z = np.meshgrid(axis, axis)
        field = N.fieldmod.Field(
            'contact-shore', 0., 0., n / 2, 1., n,
            generated(x, z).astype(np.float32), np.ones((n, n), dtype=np.uint8))
        corr = N.routemod.Corridor(
            field, cell_m=4., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        corr.soft = np.full((corr.m, corr.m), .125)
        nodes = {
            'inland': dict(xz=(18., -22.), patch=field.id, island='fixture'),
            'shore': dict(xz=(1., 8.), patch=field.id, island='fixture',
                          kind='waterfront_terminal', owner='Crossings',
                          level_y=30.6, arrive_bearing_deg=180., jetty_bearing_deg=0.)}
        edge = dict(id='contact-water', a='inland', b='shore', cls='trunk', pri=1,
                    width_m=6.)
        pads = R.PadKeepOut([], [], {})
        stack = self.enterContext(ExitStack())
        for obj, name, value in (
                (N.specmod, 'NODES', nodes), (N.specmod, 'DECLARED_CROSSINGS', []),
                (N, '_PAD_CLAIMS', []), (N, '_PAD_DATUMS', {}),
                (N, '_NODE_ANCHORS', {}), (N, '_ARRIVAL_STARTS', {}),
                (N, '_PLAN_PAD_KEEPOUTS', lambda: pads),
                (N, '_PLAN_KEEPOUTS', lambda zones: []),
                (N, '_PLAN_PLACED_OBJECTS', None),
                (N, '_PLAN_PATCHES', lambda fld, zones: {
                    f'z_{zx}_{zz}': R._flat_patch(zx, zz, generated)
                    for zx, zz in zones})):
            stack.enter_context(patch.object(obj, name, value))
        return field, corr, edge, pads

    def decode(self, record, field, pads):
        zones = sorted(set(R.segment_zones(
            record['nodes'], record['is_bridge'], record['width_m']))
                       | pads.approach_zones(record))
        patches = N._PLAN_PATCHES(field, zones)
        comps, stats = R.stamp(record, patches, zones, pads, poi_keepouts=[])
        decoded = {}
        for zone, comp in comps.items():
            parsed = R.tcdata.parse(comp.blob())
            result = R.tcdata.Compiler(zone_x=zone[0], zone_z=zone[1])
            for k, (level, smooth) in parsed['heights'].items():
                result.modified_height[k] = True
                result.level_delta[k], result.smooth_delta[k] = level, smooth
            for k, paint in parsed['paints'].items():
                result.modified_paint[k] = True
                result.paint[k] = paint
            decoded[zone] = result
        judged = R.judge_rim(record, decoded, stats, patches, _GeneratedBefore(), pads, {})
        water = R.water_sign(judged['taken_gen'], judged['taken_after'], judged['taken_now'])
        return (judged, water, pads.arrivals(record, judged['height_after']),
                R.walkability(record, decoded, patches, pads),
                sum(c.counts()['clamped'] for c in decoded.values()))

    def test_water_refusal_advances_native_contact_without_moving_landing(self):
        field, corr, edge, pads = self.fixture()
        before = _physical_snapshot(corr)
        catalog = N._bank_approach_contacts(edge, 'b', corr, None)
        joint = {}
        initial = N.build_segment(field, edge, corr, [], census=None, joint=joint)
        profile_refit.require_native_profile(initial)
        original_water = N.writer_water(initial, field)
        self.assertEqual(original_water['verdict'], 'REFUSED')
        self.assertGreater(original_water['drained'], 0)
        original_contact = initial['search_contacts']['b']

        def build(**kw):
            state = {}
            record = N.build_segment(field, edge, corr, [], census=None, joint=state, **kw)
            return record, state

        tracked = N.tracked_build(build, None)
        result, _ = N.water_route(
            field, corr, initial, joint, build=tracked,
            refusal=lambda r: N.plan_refusal(r, [], N.SEED, fld=field))
        self.assertEqual(result['writer_water']['verdict'], 'CLEAR')
        self.assertNotEqual(result['search_contacts']['b'], original_contact)
        self.assertIn(tuple(result['search_contacts']['b']), [c['xz'] for c in catalog])
        self.assertIsNone(N.arrival_geometry.fixed_landing_rejection(
            reversed(result['nodes']), (1., 8.), 0., N.BANK_LEVEL_M))
        profile_refit.require_native_profile(result)
        self.assertEqual(result['abutments'][0]['level_run_m'], 6.)
        self.assertEqual(result['abutments'][0]['level_y'], 30.6)
        self.assertEqual(result['width_m'], 6.)
        judged, water, arrivals, walk, clamped = self.decode(result, field, pads)
        self.assertEqual(judged['rim']['caused'], 0)
        self.assertEqual(water['drained_samples'], 0)
        self.assertEqual(arrivals, [])
        self.assertEqual(walk['verdict'], 'WALKABLE')
        self.assertEqual(clamped, 0)
        self.assertNotIn('bank_contacts_excluded', tracked.used[id(result)])
        ordinary_again = N.build_segment(field, edge, corr, [], census=None)
        self.assertEqual(ordinary_again['search_contacts']['b'], original_contact)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)

    def test_clear_first_native_contact_and_profile_retry_order_stay_unchanged(self):
        field, corr, edge, _pads = self.fixture(wet=False)
        catalog = N._bank_approach_contacts(edge, 'b', corr, None)
        joint = {}
        initial = N.build_segment(field, edge, corr, [], census=None, joint=joint)
        # The nearest optional connector is too short for the original freeboard
        # and owned datum. Native profile-first retry, not water, rejects it.
        self.assertNotEqual(tuple(initial['search_contacts']['b']), catalog[0]['xz'])
        self.assertEqual(N.writer_water(initial, field)['verdict'], 'CLEAR')
        calls = []

        def build(**kw):
            calls.append(kw)
            state = {}
            record = N.build_segment(field, edge, corr, [], census=None, joint=state, **kw)
            return record, state

        result, _ = N.water_route(
            field, corr, initial, joint, build=build,
            refusal=lambda r: N.plan_refusal(r, [], N.SEED, fld=field))
        self.assertEqual(result['nodes'], initial['nodes'])
        self.assertEqual(result['search_contacts'], initial['search_contacts'])
        self.assertEqual(calls, [])
        profile_refit.require_native_profile(result)

    def test_stale_contact_geometry_never_authorizes_an_inferred_retry(self):
        field, corr, edge, _pads = self.fixture()
        joint = {}
        initial = N.build_segment(field, edge, corr, [], census=None, joint=joint)
        initial['bank_contacts']['geometry_sha256'] = 'stale geometry'
        exclusions = []

        def build(**kw):
            exclusions.extend(kw.get('bank_contacts_excluded') or [])
            state = {}
            record = N.build_segment(field, edge, corr, [], census=None, joint=state, **kw)
            return record, state

        result, _ = N.water_route(
            field, corr, initial, joint, build=build,
            refusal=lambda r: N.plan_refusal(r, [], N.SEED, fld=field))
        self.assertEqual(result['writer_water']['verdict'], 'REFUSED')
        self.assertEqual(exclusions, [])

    def test_excluding_a_contact_never_unmasks_the_fixed_landing(self):
        field, corr, edge, _pads = self.fixture()
        initial = N.build_segment(field, edge, corr, [], census=None)
        selected = N._read_bank_contacts(initial)
        self.assertTrue(selected)
        corr.forbid[corr.cell_of(1., 4.)] = True
        before = _physical_snapshot(corr)
        with self.assertRaises(ValueError):
            N.build_segment(field, edge, corr, [], census=None,
                            bank_contacts_excluded=[selected])
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)

    def test_dry_preview_never_claims_an_unused_optional_bank_contact(self):
        field, corr, edge, _pads = self.fixture(wet=False)
        N.specmod.NODES['shore']['level_y'] = 31.
        result = N.build_segment(field, edge, corr, [], census=None, land_only=True)
        profile_refit.require_native_profile(result)
        self.assertEqual(result['search_contacts']['b'], [1., 8.])
        self.assertIsNone(N._read_bank_contacts(result))
        self.assertIsNone(N.arrival_geometry.fixed_landing_rejection(
            reversed(result['nodes']), (1., 8.), 0., N.BANK_LEVEL_M))

    def test_bank_exclusion_keeps_distinct_native_pad_ring_combinations(self):
        from test_pad_arrival_profiles import PadArrivalProfileTests

        case = PadArrivalProfileTests()
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.nodes['source'].update(
            kind='waterfront_terminal', owner='Crossings', level_y=40.,
            arrive_bearing_deg=90., jetty_bearing_deg=270.)
        mask = case.corr.forbid.copy()
        anchors = dict(case.anchors)
        with redirect_stdout(StringIO()):
            first = N.build_segment(
                case.field, case.edge, case.corr, [], census=None)
            selected = N._read_bank_contacts(first)
            self.assertIsInstance(selected['a'], dict)
            self.assertIsInstance(selected['b'], tuple)
            second = N.build_segment(
                case.field, case.edge, case.corr, [], census=None,
                bank_contacts_excluded=[selected])
        alternative = N._read_bank_contacts(second)
        self.assertEqual(alternative['a'], selected['a'])
        self.assertNotEqual(alternative['b'], selected['b'])
        self.assertNotEqual(second['search_contacts']['b'], first['search_contacts']['b'])
        for record in (first, second):
            profile_refit.require_native_profile(record)
            self.assertLessEqual(record['grade_limit'], N.routemod.GRADE_CEILING)
            self.assertIsNone(N.arrival_geometry.fixed_landing_rejection(
                record['nodes'], case.nodes['source']['xz'], 270., N.BANK_LEVEL_M))
            self.assertEqual(record['abutments'][0]['level_y'], 40.)
            self.assertEqual(record['abutments'][0]['level_run_m'], 6.)
        self.assertEqual(case.pad['datum_y'], 50.)
        self.assertEqual(case.anchors, anchors)
        np.testing.assert_array_equal(case.corr.forbid, mask)


if __name__ == '__main__':
    unittest.main()
