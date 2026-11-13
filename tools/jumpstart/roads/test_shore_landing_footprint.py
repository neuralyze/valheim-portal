"""A shore's fixed landing must fit its whole level road bench, not just paint."""
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import shores as S
import route as R


def surface(shoulder_height, *, graded=False):
    n = 65
    axis = np.arange(n, dtype=float) - n // 2
    x, z = np.meshgrid(axis, axis)
    heights = np.full((n, n), S.DECK_Y, dtype=float)
    heights[np.abs(x) > 2.] = shoulder_height
    if graded:
        heights += np.maximum(0., -z) * .1
    field = S.W.Field('landing', 0., 0., n / 2, 1., n,
                      heights.astype(np.float32), np.ones((n, n), np.uint8))
    return S.GeneratedSurface({'landing': field})


class ShoreLandingFootprintTests(unittest.TestCase):
    def test_submerged_outer_shoulder_is_not_an_eligible_fixed_landing(self):
        measured = surface(S.W.WATER_LEVEL - .1)
        self.assertFalse(S.approach(measured, (0., 0.), 0., 4.))

    def test_outer_shoulder_cannot_exceed_the_owned_datum_clamp(self):
        measured = surface(S.DECK_Y + R.CUT_FILL_MAX + .25)
        self.assertFalse(S.approach(measured, (0., 0.), 0., 4.))

    def test_legal_shoulder_cut_keeps_a_dry_graded_landing_eligible(self):
        measured = surface(S.DECK_Y + 5., graded=True)
        self.assertTrue(S.approach(measured, (0., 0.), 0., 4.))

    def test_dry_landing_cannot_fill_a_submerged_seaward_ground_cap(self):
        n = 65
        heights = np.full((n, n), S.DECK_Y, np.float32)
        heights[n // 2 + 1, n // 2] = S.W.WATER_LEVEL - .025
        field = S.W.Field('cap', 0., 0., n / 2, 1., n, heights,
                           np.ones((n, n), np.uint8))
        measured = S.GeneratedSurface({'cap': field})
        self.assertFalse(S.approach(measured, (0., 0.), 0., 4.))
        # Reversing the shore puts the same submerged sample inside the
        # ordinary inland landing; both orientations must refuse the fill.
        self.assertFalse(S.approach(measured, (0., 0.), 180., 4.))

    def test_shore_root_search_includes_inland_seats_for_the_full_ground_cap(self):
        n = 1801
        axis = np.arange(n, dtype=float) - n // 2
        heights = np.broadcast_to(np.where(axis[:, None] <= 0., S.DECK_Y, 20.),
                                  (n, n)).astype(np.float32)
        field = S.W.Field('coast', 0., 0., n / 2, 1., n, heights,
                           np.ones((n, n), np.uint8))
        candidates, _ = S._shore_candidates(
            field, (0., -40.), landing_half_m=R.footprint_half_m(4.))
        x = float(field.world_x(n // 2))
        inset = int(np.ceil(R.footprint_half_m(4.) / field.step))
        self.assertIn((x, float(field.world_z(n // 2 - inset))), candidates)
        self.assertNotIn((x, float(field.world_z(n // 2 + 1))), candidates)
        self.assertNotIn((x, float(field.world_z(n // 2 - 20))), candidates)


    def test_missing_mandatory_shoulder_is_unread_not_measured_clear(self):
        # Covers all original carriageway samples for the full six-metre run,
        # but not the mandatory left shoulder at x=-3.
        n = 9
        field = S.W.Field('carriageway-only', 2., -3., n / 2, 1., n,
            np.full((n, n), S.DECK_Y, np.float32), np.ones((n, n), np.uint8))
        measured = S.GeneratedSurface({'carriageway-only': field})
        with self.assertRaises(SystemExit):
            S.approach(measured, (0., 0.), 0., 4.)


if __name__ == '__main__':
    unittest.main()
