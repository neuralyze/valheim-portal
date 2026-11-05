"""Constructed flat/returning road claims, never saved-world evidence."""
import numpy as np


def constant_profile_cases(project):
    nodes = np.array([[0.0, 0.0], [12.0, 0.0], [12.0, 2.0], [0.0, 2.0]])
    bridge = np.zeros(4, dtype=bool)
    x, z = np.array([6.0, 6.0, 14.0]), np.array([1.0, 500.0, 1.0])
    rows = []
    for value in (0.0, 40.0):
        actual = project(nodes, np.full(4, value), bridge, x, z, 24.0)
        expected = (
            np.array([1.0, 498.0, 2.0]), np.full(3, value),
            np.array([6.0, 20.0, 13.0]), np.array([1, -1, -1], dtype=np.int8),
            np.array([6.0, 6.0, 12.0]), np.array([0.0, 2.0, 1.0]),
            np.full(3, np.inf), np.zeros(3), np.full(3, -np.inf))
        rows.append(dict(case=f'flat_{value:g}_hairpin_keeps_tied_and_distant_feet',
            matches_expected=all(np.array_equal(a, b) and a.dtype == b.dtype
                                 for a, b in zip(actual, expected))))
    actual = project(nodes, np.array([40.0, 40.0, 44.0, 44.0]), bridge,
                     np.array([6.0]), np.array([0.5]), 24.0)
    rows.append(dict(case='raised_returning_branch_still_sets_runner_up_and_floor',
        matches_expected=bool(actual[0][0] == 0.5 and actual[1][0] == 40.0
            and actual[6][0] == 1.5 and actual[7][0] == 44.0
            and actual[8][0] == 44.0)))
    return rows
