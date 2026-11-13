"""New deck choices must fit together before becoming immutable datums."""
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from roads import network as N


class DetectedBankLevelTests(unittest.TestCase):
    def choice(self, first, last, low, high, preferred):
        return dict(first=first, last=last, lower_y=low, upper_y=high,
                    preferred_y=preferred)

    def test_adjacent_legal_decks_choose_a_joinable_full_profile(self):
        s = np.arange(6, dtype=float)
        terrain = np.array([33.7] * 3 + [32.5] * 3)
        lo, hi = terrain - 7., terrain + 7.
        held = np.zeros(6, dtype=bool)
        choices = [self.choice(0, 2, 33.3, 35., 33.7),
                   self.choice(3, 5, 32.5, 36., 32.5)]
        heights = N._choose_bank_levels(lo, hi, held, s, choices, [.08, .25])
        self.assertIsNotNone(heights)
        for choice, height in zip(choices, heights):
            self.assertGreaterEqual(height, choice['lower_y'])
            self.assertLessEqual(height, choice['upper_y'])
            N._hold_profile(lo, hi, held,
                            slice(choice['first'], choice['last'] + 1), height)
        fitted = N.routemod.fit_ladder(
            terrain, s, lo, hi, np.ones(6, dtype=bool), [.08, .25],
            pts=np.column_stack((s, np.zeros_like(s))), width_m=6.,
            grade=.625, batter_m=12., lattice_m=1., held=held)
        self.assertTrue(fitted['terrain_feasible'])
        self.assertIn(fitted['joined']['verdict'], ('CLEAR', 'JOINED'))
        np.testing.assert_allclose(fitted['y'][:3], heights[0], atol=1e-9)
        np.testing.assert_allclose(fitted['y'][3:], heights[1], atol=1e-9)
        self.assertLessEqual(float(np.max(np.abs(np.diff(fitted['y'])))), .08 + 1e-9)

    def test_declared_bank_cannot_be_replaced_by_a_new_deck_choice(self):
        s = np.arange(5, dtype=float)
        lo, hi = np.zeros(5), np.full(5, 40.)
        held = np.zeros(5, dtype=bool)
        N._hold_profile(lo, hi, held, 0, 30.6)
        before = lo.copy(), hi.copy(), held.copy()
        result = N._choose_bank_levels(
            lo, hi, held, s, [self.choice(0, 2, 33., 35., 34.)], [.25])
        self.assertIsNone(result)
        for actual, original in zip((lo, hi, held), before):
            np.testing.assert_array_equal(actual, original)

    def test_overlapping_incompatible_decks_cannot_erase_each_other(self):
        s = np.arange(5, dtype=float)
        result = N._choose_bank_levels(
            np.zeros(5), np.full(5, 40.), np.zeros(5, dtype=bool), s,
            [self.choice(0, 2, 31., 32., 31.5),
             self.choice(2, 4, 34., 35., 34.5)], [.08, .25])
        self.assertIsNone(result)

    def test_bed_floor_is_not_lowered_to_reach_a_fixed_terminal(self):
        s = np.arange(4, dtype=float)
        lo, hi = np.zeros(4), np.full(4, 40.)
        held = np.zeros(4, dtype=bool)
        N._hold_profile(lo, hi, held, 3, 30.6)
        result = N._choose_bank_levels(
            lo, hi, held, s, [self.choice(0, 2, 33., 35., 34.)], [.08, .25])
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
