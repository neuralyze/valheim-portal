"""Road projection ownership at bridge banks; constructed, never world evidence."""
import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ribbon as R


class BankCapProjectionTests(unittest.TestCase):
    @staticmethod
    def transform(points, degrees):
        a=math.radians(degrees)
        matrix=np.array([[math.cos(a),-math.sin(a)],
                         [math.sin(a),math.cos(a)]])
        return np.asarray(points,dtype=np.float64)@matrix.T+[-296.,22.]

    def assert_no_bank_claim(self, coordinates, bridge, point):
        nodes=np.asarray(coordinates,dtype=np.float64)
        levels=np.full(len(nodes),30.6)
        br=np.asarray(bridge,dtype=bool)
        x,z=map(float,point)
        self.assertIsNone(R.lateral_and_y(nodes,levels,br,x,z))
        result=R.lateral_and_y_grid(nodes,levels,br,np.array([[x]]),
                                   np.array([[z]]),24.)
        self.assertTrue(math.isinf(float(result[0][0,0])))
        self.assertTrue(math.isinf(float(result[6][0,0])))
        self.assertEqual(float(result[8][0,0]),-math.inf)

    def test_sparse_bank_has_no_scalar_fallback_into_the_span(self):
        self.assert_no_bank_claim([[0.,0.],[6.,0.],[10.,0.]],
                                  [False,False,True],[8.,0.])

    def test_collinear_densification_cannot_reclaim_the_bridge_bank(self):
        for degrees in (0.,37.,90.,180.,270.):
            for reverse in (False,True):
                with self.subTest(degrees=degrees,reverse=reverse):
                    nodes=self.transform([[0.,0.],[2.,0.],[4.,0.],[6.,0.],[10.,0.]],degrees)
                    br=[False,False,False,False,True]
                    if reverse:
                        nodes,br=nodes[::-1],br[::-1]
                    point=self.transform([[8.,0.]],degrees)[0]
                    self.assert_no_bank_claim(nodes,br,point)

    def test_duplicate_ground_vertices_do_not_hide_the_bank_boundary(self):
        for points in ([[0.,0.],[2.,0.],[4.,0.],[4.,0.],[6.,0.],[10.,0.]],
                       [[0.,0.],[2.,0.],[4.,0.],[6.,0.],[6.,0.],[10.,0.]]):
            with self.subTest(points=points):
                self.assert_no_bank_claim(points,[False]*(len(points)-1)+[True],[8.,0.])

    def test_straight_land_road_densification_preserves_all_nine_arrays(self):
        z,x=np.mgrid[-5:6,-3:10].astype(np.float64)
        results=[]
        for nodes in ([[0.,0.],[6.,0.]],
                      [[0.,0.],[2.,0.],[4.,0.],[6.,0.]]):
            nodes=np.asarray(nodes,dtype=np.float64)
            results.append(R.lateral_and_y_grid(
                nodes,np.full(len(nodes),30.6),np.zeros(len(nodes),dtype=bool),
                x,z,24.))
        for sparse,dense in zip(*results):
            np.testing.assert_array_equal(sparse,dense)

    def test_real_corner_wedge_and_an_interior_foot_are_preserved(self):
        nodes=np.array([[0.,0.],[4.,0.],[4.,4.]])
        levels=np.full(3,30.6); br=np.zeros(3,dtype=bool)
        for point,distance,foot in (([5.,-1.],math.sqrt(2.),[4.,0.]),
                                    ([5.,1.],1.,[4.,1.])):
            with self.subTest(point=point):
                scalar=R.lateral_and_y(nodes,levels,br,*point)
                self.assertAlmostEqual(scalar[0],distance)
                actual=R.lateral_and_y_grid(nodes,levels,br,np.array([[point[0]]]),
                                            np.array([[point[1]]]),24.)
                self.assertAlmostEqual(float(actual[0][0,0]),distance)
                np.testing.assert_allclose([actual[4][0,0],actual[5][0,0]],foot)

    def test_real_other_branch_beyond_bank_plane_keeps_its_claim(self):
        nodes=np.array([[11.,-10.],[11.,10.],[0.,10.],[0.,0.],
                        [2.,0.],[4.,0.],[6.,0.],[10.,0.]])
        levels=np.full(len(nodes),30.6)
        br=np.zeros(len(nodes),dtype=bool); br[-1]=True
        scalar=R.lateral_and_y(nodes,levels,br,8.,0.)
        self.assertEqual(scalar,(3.,30.6))
        actual=R.lateral_and_y_grid(nodes,levels,br,np.array([[8.]]),np.array([[0.]]),24.)
        self.assertEqual(float(actual[0][0,0]),3.)
        self.assertEqual([float(actual[4][0,0]),float(actual[5][0,0])],[11.,0.])

    def test_true_land_terminal_and_bridge_bank_itself_still_have_ground(self):
        for nodes,br,point,distance in (
            ([[0.,0.],[2.,0.],[4.,0.],[6.,0.]],[False]*4,[8.,0.],2.),
            ([[0.,0.],[2.,0.],[4.,0.],[6.,0.],[10.,0.]],
             [False,False,False,False,True],[6.,0.],0.)):
            nodes=np.asarray(nodes,dtype=np.float64); br=np.asarray(br,dtype=bool)
            levels=np.full(len(nodes),30.6)
            self.assertEqual(R.lateral_and_y(nodes,levels,br,*point),(distance,30.6))
            actual=R.lateral_and_y_grid(nodes,levels,br,np.array([[point[0]]]),np.array([[point[1]]]),24.)
            self.assertEqual(float(actual[0][0,0]),distance)


if __name__=='__main__':
    unittest.main()
