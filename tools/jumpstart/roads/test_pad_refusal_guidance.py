"""Native pad errors must not hide reroutable own-branch conflicts."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import network as N
import ribbon as R
import test_protected_feedback as P


class ArrivalRefusalGuidanceTests(unittest.TestCase):
    def test_actual_pad_solve_cannot_hide_reroutable_own_branch_conflict(self):
        corr, raw_build, a, b = P.ProtectedFeedbackTests()._numeric_hairpin(outer_cost=100.)
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        corr.forbid |= (xx == -72.) & (abs(zz) < 30.)
        physical = corr.forbid.copy()
        pad = dict(site_id='terminal-pad', name='terminal-pad', x=b[0], z=b[1]+6.,
                   hw=4., hd=4., datum_y=66., planned=True, role='site_pad')
        pads = R.pad_keepouts_from_chain([], [], planned=[pad])
        nodes = {'a': dict(xz=a), 'b': dict(xz=(pad['x'], pad['z']))}
        grade, reach, lattice = N.writer_batter()
        pad_failures = []

        def build(guidance):
            rec, _ = raw_build(extra_guidance=guidance)
            points = np.asarray(rec['nodes'])
            s = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            y = np.asarray(rec['profile_y'])
            free = np.zeros(len(points), bool)
            cand = N.routemod.self_adjacency_lattice(
                points, free, 6., grade=grade, batter_m=reach, lattice_m=lattice, dy_cap=.25)
            rec.update(cls='trunk', pri=3, grade_limit=.25,
                       profile_constraints={'verdict':'CLEAR'}, terminal_caps=[True, True])
            joint = dict(s=s, lo=y.copy(), hi=y.copy(), y0=y.copy(), held=np.ones(len(y),bool),
                         free=free, spans=[], cand=cand, rung=.25, ladder=[.25],
                         t=np.full(len(y),60.), tw_lo=np.full(len(y),60.), tw_hi=np.full(len(y),60.))
            try:
                N._fit_pad_arrivals(rec, joint)
            except N._PadArrivalRefusal:
                pad_failures.append(N.self_adjacency_rejection(rec) is None)
                raise
            return rec, joint

        with patch.object(N.specmod, 'NODES', nodes), patch.object(N, '_PAD_CLAIMS', [pad]), \
                patch.object(N, '_PAD_DATUMS', {'terminal-pad':66.}), \
                patch.object(N, '_PLAN_PAD_KEEPOUTS', lambda:pads):
            with self.assertRaises(N._PadArrivalRefusal):
                build(None)
            result, joint = N.adjacency_reroute(corr, build)
            self.assertIsNone(N.self_adjacency_rejection(result))
            self.assertEqual((tuple(result['nodes'][0]), tuple(result['nodes'][-1])), (a,b))
            self.assertEqual(pad['datum_y'],66.)
            np.testing.assert_array_equal(corr.forbid,physical)
            np.testing.assert_array_equal(np.asarray(result['profile_y']),joint['lo'])
            gate=N.routemod._junction()
            ribbons=gate.ribbons_from_segments({'segments':[result]},'test')
            actual_pad=dict(kind='pad',id='terminal-pad',label='terminal-pad',role='site_pad',
                            x=pad['x'],z=pad['z'],hw=pad['hw'],hd=pad['hd'],datum_y=66.)
            pairs=gate.find_conflicts(ribbons,[actual_pad],('pad_datums',))['pairs']
            self.assertTrue(pairs)
            self.assertTrue(all(q['spread_m'] <= q['slide_allow_m']+1e-6 for q in pairs))

            # A real pad-height contradiction must still refuse, even after
            # guidance finds the geometrically clear bypass.
            pad['datum_y'] = 80.
            pads = R.pad_keepouts_from_chain([], [], planned=[pad])
            pad_failures.clear()
            with patch.object(N, '_PAD_DATUMS', {'terminal-pad':80.}):
                with self.assertRaises(N._PadArrivalRefusal):
                    N.adjacency_reroute(corr, build)
            self.assertIn(True, pad_failures)
            self.assertEqual(pad['datum_y'],80.)
            np.testing.assert_array_equal(corr.forbid,physical)


if __name__=='__main__':
    unittest.main()
