"""Terminal ownership affects native constraints, not stations or grade bounds."""
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import route as R


GRADE = 0.625
BATTER = 4.0
LATTICE = 1.0
WIDTH = 4.0
WRITER_TERMS = ("every_participants_own_design_height",
                "the_writers_claim_boundary_between_legs",
                "each_legs_own_bounds_and_branches")


def candidate(nodes, bridge=None, **kwargs):
    return R.self_adjacency_lattice(
        nodes, bridge, WIDTH, grade=GRADE, batter_m=BATTER,
        lattice_m=LATTICE, dy_cap=0.25, **kwargs)


def leg(name, nodes, *, height=40.0, bridge=None, width=2.0):
    nodes = np.asarray(nodes, dtype=np.float64)
    bridge = (np.zeros(len(nodes), dtype=bool) if bridge is None
              else np.asarray(bridge, dtype=bool))
    y = np.full(len(nodes), height)
    return {"id": name, "cls": "spur", "pri": 0, "width_m": width,
            "nodes": nodes, "is_bridge": bridge, "y0": y,
            "s": np.asarray(R._writer().node_arcs(nodes, bridge)),
            "lo": y.copy(), "hi": y.copy(), "spans": [], "cand": None,
            "rung": 0.25, "ladder": [0.25]}


class RouteTerminalCapsTests(unittest.TestCase):
    # A real return branch lies beyond the bank's plane.  Closing the bank
    # must remove its fan, not cut the other branch out of the footprint.
    bank_nodes = np.array([[11., -10.], [11., 10.], [0., 10.], [0., 0.],
                           [2., 0.], [4., 0.], [6., 0.]])

    def assert_same_edges(self, a, b, *, arc_shift=0.0):
        self.assertEqual(set(a), set(b))
        for key in a:
            expected = b[key] - arc_shift if key in ("s1", "s2") else b[key]
            np.testing.assert_allclose(a[key], expected, atol=1e-12, rtol=0,
                                       err_msg=key)

    @staticmethod
    def has_edge(edges, start, end):
        return bool(np.any((edges["x1"] == start[0])
                           & (edges["z1"] == start[1])
                           & (edges["x2"] == end[0])
                           & (edges["z2"] == end[1])))

    def test_self_adjacency_matches_real_marker_at_either_terminal(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                nodes = self.bank_nodes[::-1] if reverse else self.bank_nodes
                marked = np.vstack(([10., 0.], nodes)) if reverse else np.vstack(
                    (nodes, [10., 0.]))
                bridge = np.zeros(len(marked), dtype=bool)
                bridge[0 if reverse else -1] = True
                caps = (False, True) if reverse else (True, False)
                ordinary = candidate(nodes)
                closed = candidate(nodes, terminal_caps=caps)
                real_marker = candidate(marked, bridge)
                self.assert_same_edges(closed["edges"], real_marker["edges"],
                                       arc_shift=4.0 if reverse else 0.0)
                self.assertTrue(self.has_edge(ordinary["edges"], (8., 0.), (9., 0.)))
                self.assertFalse(self.has_edge(closed["edges"], (8., 0.), (9., 0.)))
                self.assertTrue(self.has_edge(closed["edges"], (6., 0.), (7., 0.)))
                self.assertGreater(len(closed["edges"]["s1"]), 0)
                np.testing.assert_array_equal(closed["pts"], nodes)
                self.assertEqual(len(closed["arcs"]), len(nodes))

    def test_default_and_none_preserve_existing_candidate_geometry(self):
        default = candidate(self.bank_nodes)
        for caps in ((True, True), None, np.array([True, True])):
            with self.subTest(caps=caps):
                actual = candidate(self.bank_nodes, terminal_caps=caps)
                self.assert_same_edges(default["edges"], actual["edges"])
                np.testing.assert_array_equal(default["near1"], actual["near1"])
                np.testing.assert_array_equal(default["near2"], actual["near2"])
                np.testing.assert_array_equal(default["allow_e"], actual["allow_e"])

    def test_ladder_does_not_refuse_fixed_ground_for_a_ghost_terminal_fan(self):
        nodes = self.bank_nodes
        s = np.asarray(R._writer().node_arcs(nodes, np.zeros(len(nodes), bool)))
        y = 40.0 + 0.02 * s

        def fit(**kwargs):
            return R.fit_ladder(
                y, s, y.copy(), y.copy(), np.zeros(len(nodes), dtype=bool),
                [0.25], pts=nodes, width_m=WIDTH, grade=GRADE,
                batter_m=BATTER, lattice_m=LATTICE,
                held=np.ones(len(nodes), dtype=bool), **kwargs)

        ordinary = fit()
        closed = fit(terminal_caps=np.array([True, False]))
        self.assertEqual(ordinary["joined"]["verdict"], "REFUSED")
        self.assertGreater(ordinary["joined"]["pairs_before"], 0)
        self.assertEqual(closed["joined"]["verdict"], "CLEAR")
        self.assertTrue(closed["terrain_feasible"])
        self.assertEqual(closed["rung"], 0.25)
        self.assertEqual(closed["cand"]["terminal_caps"], (True, False))
        np.testing.assert_array_equal(closed["y"], y)
        self.assertEqual(len(closed["y"]), len(nodes))
        self.assertTrue(np.all(np.abs(np.diff(closed["y"])) <= 0.25 * np.diff(s)))
        marked = np.vstack((nodes, [10., 0.]))
        bridge = np.zeros(len(marked), dtype=bool)
        bridge[-1] = True
        self.assert_same_edges(closed["cand"]["edges"],
                               candidate(marked, bridge)["edges"])

    def readings(self, legs):
        return R._joint_readings(legs, [], WRITER_TERMS, grade=GRADE,
                                 batter_m=2.0, lattice_m=LATTICE)

    def solve(self, legs):
        return R.junction_datums(legs, [], grade=GRADE, batter_m=2.0,
                                 lattice_m=LATTICE, depends_on=WRITER_TERMS)

    def test_joint_reader_removes_only_cap_claims_for_all_record_forms(self):
        # The two real terrain footprints have a one-cell gap.  Only the
        # ordinary terminal fan makes them meet; the height disagreement is
        # deliberately immutable, so dropping a false claim cannot buy grade.
        for source in ("leg", "candidate", "none_leg"):
            for caps in ((True, False), [True, False], np.array([True, False])):
                with self.subTest(source=source, caps=caps):
                    bank = leg("bank", [[0., 0.], [6., 0.]])
                    other = leg("other", [[12., -6.], [12., 6.]], height=50.)
                    original = self.readings([bank, other])
                    self.assertGreater(len(original["pairs"]["la"]), 0)
                    self.assertTrue(self.solve([bank, other])["refused"])
                    if source == "leg":
                        bank["terminal_caps"] = caps
                    else:
                        bank["cand"] = R.self_adjacency_lattice(
                            bank["nodes"], bank["is_bridge"], bank["width_m"],
                            grade=GRADE, batter_m=2., lattice_m=LATTICE,
                            dy_cap=0.25, terminal_caps=caps)
                        if source == "none_leg":
                            bank["terminal_caps"] = None
                    closed = self.readings([bank, other])
                    self.assertEqual(len(closed["pairs"]["la"]), 0)
                    solved = self.solve([bank, other])
                    self.assertEqual(solved["refused"], [])
                    for lg in (bank, other):
                        np.testing.assert_array_equal(solved["y"][lg["id"]], lg["y0"])
                        self.assertEqual(solved["rung"][lg["id"]], 0.25)
                    marked = leg("bank", [[0., 0.], [6., 0.], [10., 0.]],
                                 bridge=[False, False, True])
                    self.assertEqual(len(self.readings([marked, other])["pairs"]["la"]), 0)

    def test_explicit_leg_caps_override_candidate_without_array_truthiness(self):
        bank = leg("bank", [[0., 0.], [6., 0.]])
        other = leg("other", [[12., -6.], [12., 6.]])
        bank["cand"] = R.self_adjacency_lattice(
            bank["nodes"], bank["is_bridge"], bank["width_m"], grade=GRADE,
            batter_m=2., lattice_m=LATTICE, dy_cap=0.25,
            terminal_caps=np.array([True, False]))
        self.assertEqual(len(self.readings([bank, other])["pairs"]["la"]), 0)
        bank["terminal_caps"] = np.array([True, True])
        self.assertGreater(len(self.readings([bank, other])["pairs"]["la"]), 0)

    def test_real_ground_joint_readings_survive_and_match_marker(self):
        for reverse in (False, True):
            for varying in (False, True):
                with self.subTest(reverse=reverse, varying=varying):
                    nodes = [[0., 0.], [3., 0.], [6., 0.]]
                    marked_nodes = nodes + [[10., 0.]]
                    bridge = [False, False, False, True]
                    if reverse:
                        nodes, marked_nodes, bridge = nodes[::-1], marked_nodes[::-1], bridge[::-1]
                    bank = leg("bank", nodes)
                    bank["terminal_caps"] = np.array([False, True] if reverse else [True, False])
                    marked = leg("bank", marked_nodes, bridge=bridge)
                    if varying:
                        # Same physical profile, independent of marker arc and
                        # station indexing, with no extra ground variable.
                        bank["y0"] = 40. + 0.02 * bank["nodes"][:, 0]
                        marked["y0"] = 40. + 0.02 * marked["nodes"][:, 0]
                        for lg in (bank, marked):
                            lg["lo"] = lg["y0"].copy()
                            lg["hi"] = lg["y0"].copy()
                    crossing = leg("crossing", [[3., -8.], [3., 8.]], height=43.)
                    actual = self.readings([bank, crossing])["pairs"]
                    expected = self.readings([marked, crossing])["pairs"]
                    self.assertGreater(len(actual["la"]), 0)
                    for key in ("la", "lb", "allow", "kind", "x", "z"):
                        np.testing.assert_allclose(actual[key], expected[key], atol=1e-12, rtol=0)
                    for suffix in ("a", "b"):
                        values = []
                        for lg, rows in ((bank, actual), (marked, expected)):
                            values.append(R._values(
                                [lg["y0"], crossing["y0"]], rows["l" + suffix],
                                rows["k0" + suffix], rows["k1" + suffix], rows["t" + suffix]))
                        np.testing.assert_allclose(*values, atol=1e-12, rtol=0)
                    # An incompatible actual crossing remains a refusal even
                    # though the distant bank continuation owns no terrain.
                    self.assertTrue(self.solve([bank, crossing])["refused"])


if __name__ == "__main__":
    unittest.main()
