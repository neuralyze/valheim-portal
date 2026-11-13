"""A supportable dock must not put its measured solids through its boat."""
from pathlib import Path
import math
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent/'crossings')]
import assemble as A
from accept.sections import harbours
from shore_solids import boat_clearance


class ShoreSolidTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hulls = harbours.hull_table()
        cls.geometry = harbours._corpus()[0]
        cls.hull = cls.hulls['MercantShip']

    def boat(self, yaw=0.0):
        return dict(prefab=self.hull.name, x=12.5, y=30.0, z=-7.25, yaw=yaw)

    def pillar(self, boat, *, local_x=None, local_z=None, y=None):
        hull = self.hull
        x = (sum(v[0] for v in hull.profile)/len(hull.profile)
             if local_x is None else local_x)
        z = (sum(v[1] for v in hull.profile)/len(hull.profile)
             if local_z is None else local_z)
        angle = math.radians(boat['yaw'])
        ux, uz = math.sin(angle), math.cos(angle)
        height = boat['y'] - hull.draught_m/2 if y is None else y
        return A.Placed('stone_pillar', boat['x'] + x*uz + z*ux,
                        height, boat['z'] - x*ux + z*uz,
                        boat['yaw'], 'dock/pile')

    def judge(self, pieces, boat, **kwargs):
        return boat_clearance(pieces, boat, hulls=self.hulls,
                              geometry=kwargs.get('geometry', self.geometry))

    def test_measured_pillar_inside_full_hull_is_refused_at_rotated_pose(self):
        for yaw in (0.0, 37.0, 90.0):
            with self.subTest(yaw=yaw):
                boat = self.boat(yaw)
                verdict = self.judge([self.pillar(boat)], boat)
                self.assertFalse(verdict['ok'])
                self.assertTrue(any(part['prefab'] == 'stone_pillar'
                                    for part in verdict['blocked']))

    def test_separated_and_above_air_draft_pillars_keep_clear_berth(self):
        boat = self.boat(37.0)
        far_x = max(abs(p[0]) for p in self.hull.profile) + 10.0
        separate = self.pillar(boat, local_x=far_x)
        above = self.pillar(boat, y=boat['y'] + self.hull.air_m + 10.0)
        verdict = self.judge([separate, above], boat)
        self.assertTrue(verdict['ok'], verdict)
        self.assertEqual(verdict['blocked'], [])

    def test_native_representable_keel_overlap_is_not_ledger_tolerance(self):
        boat = self.boat()
        keel = boat['y'] - self.hull.draught_m
        pillar_top = A.facts()['stone_pillar'].hi[1]
        piece = self.pillar(boat, y=keel + .005 - pillar_top)
        verdict = self.judge([piece], boat)
        self.assertFalse(verdict['ok'], verdict)
        self.assertTrue(any(part['y_hi'] > keel for part in verdict['blocked']))

    def test_missing_foundation_geometry_is_not_a_clearance_pass(self):
        boat = self.boat()
        verdict = self.judge([self.pillar(boat)], boat, geometry={})
        self.assertFalse(verdict['ok'])
        self.assertEqual(verdict['missing_geometry'], ['stone_pillar'])

    def test_missing_measured_hull_is_not_a_clearance_pass(self):
        boat = self.boat()
        verdict = boat_clearance([self.pillar(boat)], boat,
                                 hulls={}, geometry=self.geometry)
        self.assertFalse(verdict['ok'])
        self.assertEqual(verdict['missing_geometry'], [boat['prefab']])


if __name__ == '__main__':
    unittest.main()
