"""A conservative station envelope cannot prove exact lattice-foot infeasibility."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import route as R

# First 26 stations of the captured W1 bank counterexample, translated by
# integer x/z offsets and its fixed height datum. No world input is required.
CASE = {'points': [[0.0, 0.0],
            [-1.812366279990556, -0.8457709306622547],
            [-3.624732559981055, -1.6915418613244952],
            [-5.437098839971611, -2.53731279198675],
            [-7.24946511996211, -3.3830837226489763],
            [-8.834341737018406, -2.1656582629816086],
            [-10.248555299391512, -0.7514447006085163],
            [-11.662768861764619, 0.662768861764576],
            [-13.076982424137668, 2.0769824241376824],
            [-14.491195986510775, 3.4911959865107747],
            [-15.5, 5.073335682347135],
            [-15.5, 7.073335682347135],
            [-15.5, 9.073335682347135],
            [-15.5, 11.073335682347135],
            [-15.905409548883881, 12.905409548883881],
            [-17.319623111256988, 14.319623111256973],
            [-18.733836673630094, 15.733836673630066],
            [-20.148050236003144, 17.148050236003158],
            [-21.56226379837625, 18.56226379837625],
            [-22.976477360749357, 19.976477360749357],
            [-23.5, 19.24037281663763],
            [-23.5, 17.24037281663763],
            [-24.390690923122463, 17.39069092312245],
            [-25.804904485495513, 18.80490448549554],
            [-27.21911804786862, 20.219118047868633],
            [-28.633331610241726, 21.633331610241726]],
 's': [0.0,
       2.0000000000000258,
       3.999999999999994,
       6.0000000000000195,
       7.999999999999982,
       9.99848908957193,
       11.998489089571937,
       13.998489089571944,
       15.998489089571919,
       17.998489089571926,
       19.87488231988724,
       21.87488231988724,
       23.87488231988724,
       25.87488231988724,
       27.751275550202553,
       29.75127555020256,
       31.751275550202568,
       33.751275550202536,
       35.75127555020254,
       37.75127555020256,
       38.654561691837586,
       40.654561691837586,
       41.55784783347263,
       43.55784783347259,
       45.5578478334726,
       47.55784783347261],
 'lo': [0.0,
        0.0,
        0.0,
        0.0,
        0.3999999999999986,
        0.3999999999999986,
        0.40776429062791664,
        0.4906448401907113,
        0.6823753246757605,
        1.0336034401528948,
        1.5472791180970233,
        1.7735641072444608,
        1.3808875848296864,
        1.1899362561866411,
        1.1286460495201993,
        1.4346223054382463,
        1.3488240121453927,
        1.4506355753587599,
        1.500496810872285,
        1.394896143949893,
        1.9934347874764953,
        2.030836546970889,
        3.1346939875373323,
        3.54533322730898,
        4.338142235583945,
        4.781687239569159],
 'hi': [0.0,
        0.0,
        0.0,
        0.0,
        10.509193555020289,
        10.32390263079781,
        10.559041785356754,
        10.943174180686427,
        11.700574111998996,
        11.907984828451212,
        11.971416226890426,
        11.682042479566633,
        11.44868462155734,
        11.204050107989382,
        10.429088774660812,
        10.458529441258662,
        10.828457221665609,
        11.399401275864413,
        11.707129591169142,
        12.339334685062205,
        13.053894897949057,
        13.791414830592558,
        13.522431582456498,
        13.568296992898546,
        13.871651522105367,
        13.955003923893287],
 'y0': [0.0,
        0.0,
        0.0,
        0.0,
        0.49999999999998934,
        0.9996222723929762,
        1.4996222723929762,
        1.9996222723929762,
        2.499622272392969,
        2.999622272392969,
        3.4687205799717944,
        3.9687205799717944,
        4.468720579971794,
        4.968720579971794,
        5.43781888755062,
        5.93781888755062,
        6.43781888755062,
        6.558153649952814,
        6.844243316955655,
        7.0930753397732715,
        7.318896875182027,
        7.818896875182027,
        8.044718410590782,
        8.361236056580445,
        8.861236056580445,
        9.361236056580445],
 'held': [True,
          True,
          True,
          True,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False,
          False]}


class ExactSelfJoinTests(unittest.TestCase):
    def fixture(self):
        points = np.asarray(CASE['points'], float)
        s, lo, hi, y0 = (np.asarray(CASE[key], float) for key in ('s', 'lo', 'hi', 'y0'))
        free = np.zeros(len(s), dtype=bool)
        cand = R.self_adjacency_lattice(
            points, free, 8., grade=.7813, batter_m=21., lattice_m=1.,
            dy_cap=.25, terminal_caps=(False, True))
        return points, s, lo, hi, y0, free, cand

    def check_profile(self, y, s, lo, hi, cand):
        self.assertEqual(R.self_adjacency_violations(y, cand), [])
        self.assertTrue(np.all(y >= lo - 1e-9))
        self.assertTrue(np.all(y <= hi + 1e-9))
        self.assertTrue(np.all(np.abs(np.diff(y)) <= .25 * np.diff(s) + 1e-9))
        held = np.asarray(CASE['held'], bool)
        np.testing.assert_array_equal(y[held], lo[held])

    def test_fractional_foot_constraints_do_not_pin_nearest_stations(self):
        points, s, lo, hi, y0, free, cand = self.fixture()
        self.assertGreater(len(R.self_adjacency_violations(y0, cand)), 0)
        result = R.join_branches(y0, s, .25, lo, hi, R.station_pairs(cand, .25))
        self.assertEqual(result['verdict'], 'JOINED')
        self.check_profile(result['y'], s, lo, hi, cand)

    def test_infeasible_exact_readings_cannot_move_fixed_datums(self):
        points, s, lo, hi, y0, free, cand = self.fixture()
        result = R.join_branches(y0, s, .25, y0, y0, R.station_pairs(cand, .25))
        self.assertEqual(result['verdict'], 'UNJOINABLE')
        np.testing.assert_array_equal(result['y'], y0)

    def joint_legs(self):
        points, s, lo, hi, y0, free, cand = self.fixture()
        leg = dict(id='fractional-bank', cls='spur', pri=1, width_m=8.,
                   nodes=points, is_bridge=free, s=s, lo=lo, hi=hi, y0=y0,
                   spans=[], rung=.25, ladder=[.25], cand=cand,
                   held=np.asarray(CASE['held'], bool), terminal_caps=(False, True))
        other = dict(id='unshared', cls='spur', pri=1, width_m=8.,
                     nodes=np.array([[100., 0.], [120., 0.]]),
                     is_bridge=np.zeros(2, bool), s=np.array([0., 20.]),
                     lo=np.full(2, 5.), hi=np.full(2, 5.), y0=np.full(2, 5.),
                     spans=[], rung=.25, ladder=[.25])
        return leg, other

    def test_joint_closure_uses_exact_feet_and_preserves_an_unshared_leg(self):
        leg, other = self.joint_legs()
        result = R.junction_datums([leg, other], [], grade=.7813,
                                  batter_m=21., lattice_m=1.)
        self.assertEqual(result['refused'], [])
        self.check_profile(result['y'][leg['id']], leg['s'], leg['lo'], leg['hi'],
                           leg['cand'])
        np.testing.assert_array_equal(result['y'][other['id']], other['y0'])

    def test_component_scope_finds_the_live_infeasible_component(self):
        leg, other = self.joint_legs()
        other['id'] = 'a-unshared'
        leg['lo'] = leg['hi'] = leg['y0']
        with patch.object(R, '_CONFLICT_SCOPE', 'component'):
            result = R.junction_datums([other, leg], [], grade=.7813,
                                      batter_m=21., lattice_m=1.)
        self.assertEqual([row['id'] for row in result['refused']], [leg['id']])
        np.testing.assert_array_equal(result['y'][other['id']], other['y0'])

    def margin_legs(self):
        # Ten native turn stations, integer-translated to preserve lattice
        # phase. The incoming profile fits the physical allowance, but not
        # its existing 1 cm safety margin; a legal nearby profile exists.
        points = np.array([
            [-25.9680374315354, .0319625684645],
            [-27.3822509939085, -1.3822509939087],
            [-29.3334777586296, -1.5], [-31.3334777586296, -1.5],
            [-31.5, -1.5], [-32.5574495561975, -1.1654424983785],
            [-34.4642904295558, -.56215327778],
            [-35.3095269530372, -2.3747688518533],
            [-36.1547634765186, -4.1873844259267], [-37., -6.]])
        s = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        y = np.array([1.838718925211695, 1.80760252782994, 1.31890843155145,
                      .81890843155145, .77727787120884, .5, 0., 0., 0., 0.])
        free = np.zeros(len(s), dtype=bool)
        lo, hi = np.full(len(s), .4), np.full(len(s), 10.)
        lo[-4:] = hi[-4:] = 0.
        held = np.zeros(len(s), dtype=bool)
        held[-4:] = True
        cand = R.self_adjacency_lattice(
            points, free, 6., grade=.7812856265067174, batter_m=21.,
            lattice_m=1., dy_cap=.25, terminal_caps=(False, False))
        leg = dict(id='margin-turn', cls='spur', pri=1, width_m=6.,
                   nodes=points, is_bridge=free, s=s, lo=lo, hi=hi, y0=y,
                   spans=[], rung=.25, ladder=[.25], cand=cand, held=held,
                   terminal_caps=(False, False))
        return leg

    def test_joint_preserves_native_self_margin_without_moving_owned_datums(self):
        leg = self.margin_legs()
        result = R.junction_datums([leg], [], grade=.7812856265067174,
                                  batter_m=21., lattice_m=1.)
        self.assertEqual(result['refused'], [])
        y, cand = result['y'][leg['id']], leg['cand']
        a = np.interp(cand['edges']['s1'], leg['s'], y)
        b = np.interp(cand['edges']['s2'], leg['s'], y)
        limit = np.maximum(cand['allow_e'] - R.SELF_ADJACENCY_JOIN_MARGIN_M, 0.)
        self.assertTrue(np.all(np.abs(a-b) <= limit + 1e-9))
        np.testing.assert_array_equal(y[leg['held']], leg['lo'][leg['held']])
        self.assertTrue(np.all(y >= leg['lo'] - 1e-9))
        self.assertTrue(np.all(y <= leg['hi'] + 1e-9))
        self.assertTrue(np.all(np.abs(np.diff(y)) <= .25*np.diff(leg['s']) + 1e-9))

    def test_joint_refuses_when_owned_profile_cannot_keep_native_self_margin(self):
        leg = self.margin_legs()
        leg['lo'] = leg['hi'] = leg['y0']
        original = leg['y0'].copy()
        result = R.junction_datums([leg], [], grade=.7812856265067174,
                                  batter_m=21., lattice_m=1.)
        self.assertEqual([row['id'] for row in result['refused']], [leg['id']])
        self.assertNotIn(leg['id'], result['y'])
        np.testing.assert_array_equal(leg['y0'], original)


if __name__ == '__main__':
    unittest.main()
