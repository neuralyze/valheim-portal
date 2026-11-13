"""A snapped bank contact is not the only legal contact with that owned datum."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import network as N
from test_span_remedy_feedback import _corridor, _physical_snapshot


class BankProfileContactsTests(unittest.TestCase):
    def test_all_contacts_keep_the_same_bank_and_original_terminal(self):
        corr = _corridor()
        origin = tuple(map(float, corr.cell_world(*corr.cell_of(0., 0.))))
        anchor = (origin[0] + .5, origin[1] + .5)
        before = _physical_snapshot(corr)
        edge = dict(a='owned-bank', b='elsewhere', cls='trunk', width_m=6.)
        with patch.object(N.specmod, 'NODES', {'owned-bank': dict(xz=anchor, level_y=30.6)}):
            contacts = N._bank_profile_contacts(edge, 'a', corr, origin, None)
        expected = {origin, (origin[0] + 4., origin[1]), (origin[0], origin[1] + 4.)}
        self.assertEqual(set(contacts), expected)
        for name, value in before.items():
            np.testing.assert_array_equal(getattr(corr, name), value)

    def test_physical_and_leg_local_masks_still_reject_contacts(self):
        corr = _corridor()
        origin = tuple(map(float, corr.cell_world(*corr.cell_of(0., 0.))))
        anchor = (origin[0] + .5, origin[1] + .5)
        extra = np.zeros((corr.m, corr.m), dtype=bool)
        extra[corr.cell_of(origin[0] + 4., origin[1])] = True
        corr.forbid[corr.cell_of(origin[0], origin[1] + 4.)] = True
        edge = dict(a='owned-bank', b='elsewhere', cls='trunk', width_m=6.)
        with patch.object(N.specmod, 'NODES', {'owned-bank': dict(xz=anchor, level_y=30.6)}):
            self.assertEqual(N._bank_profile_contacts(edge, 'a', corr, origin, extra), [origin])
        corr.wet[corr.cell_of(*origin)] = True
        with patch.object(N.specmod, 'NODES', {'owned-bank': dict(xz=anchor, level_y=30.6)}):
            self.assertEqual(N._bank_profile_contacts(edge, 'a', corr, origin, extra), [])

    def test_an_ordinary_terminal_is_not_a_declared_bank(self):
        corr = _corridor()
        origin = tuple(map(float, corr.cell_world(*corr.cell_of(0., 0.))))
        edge = dict(a='ordinary', b='elsewhere', cls='trunk', width_m=6.)
        with patch.object(N.specmod, 'NODES', {'ordinary': dict(xz=origin)}):
            self.assertEqual(N._bank_profile_contacts(edge, 'a', corr, origin, None), [])

    def test_restored_bank_can_enter_a_diagonal_cell_touching_its_footprint(self):
        corr = _corridor()
        origin = tuple(map(float, corr.cell_world(*corr.cell_of(0., 0.))))
        anchor = (origin[0] - .5, origin[1] - .5)
        diagonal = (origin[0] - corr.cell_m, origin[1] - corr.cell_m)
        outside = (origin[0] - 2 * corr.cell_m, origin[1] - 2 * corr.cell_m)
        before = _physical_snapshot(corr)
        edge = dict(a='owned-bank', b='elsewhere', cls='trunk', width_m=6.)
        node = dict(xz=anchor, level_y=30.6, kind='abutment', crossing='C')
        extra = np.zeros((corr.m, corr.m), dtype=bool)
        extra[corr.cell_of(*diagonal)] = True
        with patch.object(N.specmod, 'NODES', {'owned-bank': node}):
            contacts = N._bank_profile_contacts(edge, 'a', corr, origin, None)
            self.assertIn(diagonal, contacts)
            self.assertNotIn(outside, contacts)
            masked = N._bank_profile_contacts(edge, 'a', corr, origin, extra)
            self.assertEqual(set(contacts) - set(masked), {diagonal})
        for name, value in before.items():
            np.testing.assert_array_equal(getattr(corr, name), value)


if __name__ == '__main__':
    unittest.main()
