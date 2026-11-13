"""Real terminal-pad relations participate outside the collision gate's band."""
from copy import deepcopy
from pathlib import Path
import math
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import route


def pad(pad_id="arrival-pad", *, x=-8.0, z=8.0, datum=46.0):
    return {"kind": "pad", "id": pad_id, "label": pad_id,
            "role": "site_pad", "x": x, "z": z, "hw": 2.0, "hd": 2.0,
            "datum_y": datum, "source": "constructed-arrival-case"}


def leg(leg_id="arrival-leg", *, z=0.0):
    return {"id": leg_id, "cls": "spur", "pri": 0, "width_m": 4.0,
            "nodes": [[0.0, z], [10.0, z], [20.0, z]],
            "is_bridge": [False, False, False],
            "y0": np.full(3, 40.0), "s": np.array([0.0, 10.0, 20.0]),
            "lo": np.full(3, 39.0), "hi": np.full(3, 47.0),
            "spans": [], "cand": None, "rung": 0.12,
            "ladder": [0.12, 0.18, 0.25]}


def arrival(lg, participant, station=0):
    x, z = map(float, lg["nodes"][station])
    gap = math.hypot(max(0.0, abs(x - participant["x"]) - participant["hw"]),
                     max(0.0, abs(z - participant["z"]) - participant["hd"]))
    allow = route._junction().slide_allow(
        max(0.0, gap - route.footprint_half_m(lg["width_m"])))
    return {"station": station, "pad": participant["id"],
            "datum": participant["datum_y"], "allow": allow, "x": x, "z": z}


class RouteArrivalReadingsTests(unittest.TestCase):
    def solve(self, legs, pads, *, depends_on=route.JUNCTION_DATUM_DEPENDS_ON):
        return route.junction_datums(legs, pads, grade=0.25, batter_m=4.0,
                                     lattice_m=1.0, depends_on=depends_on)

    def assert_legal(self, result, lg, reading):
        y = result["y"][lg["id"]]
        rung = result["rung"][lg["id"]]
        self.assertTrue(np.all(y >= lg["lo"] - 1e-9))
        self.assertTrue(np.all(y <= lg["hi"] + 1e-9))
        self.assertTrue(np.all(np.abs(np.diff(y)) <= rung * np.diff(lg["s"]) + 1e-9))
        emitted = float(np.round(y[reading["station"]], 3))
        self.assertLessEqual(abs(emitted - reading["datum"]),
                             reading["allow"] + route.JUNCTION_DATUM_EPS_M)
        self.assertEqual(result["legs"][lg["id"]]["over_allowance_after"], 0)

    def test_noncolliding_terminal_is_fitted_at_either_end(self):
        for station in (0, 2):
            with self.subTest(station=station):
                lg = leg()
                participant = pad(x=-8.0 if station == 0 else 28.0)
                bare = self.solve([lg], [participant])
                self.assertEqual(bare["legs"][lg["id"]]["verdict"], "UNSHARED")
                reading = arrival(lg, participant, station)
                lg["arrival_readings"] = [reading]
                result = self.solve([lg], [participant])
                self.assertEqual(result["refused"], [])
                self.assertEqual(result["legs"][lg["id"]]["verdict"], "JOINED")
                self.assertEqual(result["legs"][lg["id"]]["shares_ground_with"],
                                 [participant["id"]])
                self.assert_legal(result, lg, reading)

    def test_infeasible_arrival_is_refused_with_real_pad_and_coordinates(self):
        lg, participant = leg(), pad()
        lg["lo"] = lg["hi"] = lg["y0"].copy()
        reading = arrival(lg, participant)
        lg["arrival_readings"] = [reading]
        unrelated = leg("unrelated-leg", z=100.0)
        result = self.solve([unrelated, lg], [participant])
        self.assertNotIn(lg["id"], result["y"])
        self.assertEqual([r["id"] for r in result["refused"]], [lg["id"]])
        binding = result["refused"][0]["binding"]
        self.assertEqual(binding["with"], participant["id"])
        self.assertEqual(binding["reading"], "pad datum")
        self.assertEqual(binding["xz"], [reading["x"], reading["z"]])
        self.assertEqual(binding["design_y"], [40.0, participant["datum_y"]])
        self.assertEqual(binding["allowance_m"], round(reading["allow"], 4))
        self.assertGreater(binding["spread_m"], binding["allowance_m"])
        self.assertEqual(binding["rungs_tried"][lg["id"]], [0.12, 0.18, 0.25])
        self.assertEqual(result["legs"][unrelated["id"]]["verdict"], "UNSHARED")
        np.testing.assert_array_equal(result["y"][unrelated["id"]], unrelated["y0"])

    def test_arrival_can_climb_only_its_own_grade_ladder(self):
        lg, participant = leg(), pad()
        lg["rung"] = 0.08
        lg["ladder"] = [0.08, 0.12, 0.18, 0.25]
        lg["hi"][-1] = lg["lo"][-1] = 40.0
        reading = arrival(lg, participant)
        lg["arrival_readings"] = [reading]
        result = self.solve([lg], [participant])
        self.assertEqual(result["refused"], [])
        self.assertEqual(result["rung"][lg["id"]], 0.12)
        self.assertEqual(result["legs"][lg["id"]]["rungs_climbed"], [0.08])
        self.assert_legal(result, lg, reading)

    def test_native_margin_is_reserved_once_not_twice(self):
        lg, participant = leg(), pad()
        reading = arrival(lg, participant)
        limit = reading["datum"] - reading["allow"] + route.JUNCTION_DATUM_MARGIN_M
        lg["hi"][:] = limit
        lg["arrival_readings"] = [reading]
        result = self.solve([lg], [participant])
        self.assertEqual(result["refused"], [])
        self.assertAlmostEqual(result["y"][lg["id"]][0], limit, places=9)
        self.assert_legal(result, lg, reading)

    def test_disabled_pad_datums_preserve_unshared_design_even_for_missing_pad(self):
        lg = leg()
        lg["arrival_readings"] = [arrival(lg, pad("missing-pad"))]
        terms = tuple(t for t in route.JUNCTION_DATUM_DEPENDS_ON if t != "pad_datums")
        result = self.solve([lg], [], depends_on=terms)
        self.assertEqual(result["refused"], [])
        self.assertEqual(result["legs"][lg["id"]]["verdict"], "UNSHARED")
        np.testing.assert_array_equal(result["y"][lg["id"]], lg["y0"])

    def test_unrelated_pad_without_arrival_relation_does_not_constrain_leg(self):
        lg, participant = leg(), pad()
        lg["arrival_readings"] = [arrival(lg, participant)]
        unrelated = pad("unrelated-pad", x=-8.0, z=-8.0, datum=100.0)
        expected = self.solve([lg], [participant])
        result = self.solve([lg], [participant, unrelated])
        self.assertEqual(result["refused"], [])
        np.testing.assert_array_equal(result["y"][lg["id"]], expected["y"][lg["id"]])
        self.assertNotIn(unrelated["id"], result["legs"][lg["id"]]["shares_ground_with"])

    def test_explicit_arrival_keeps_ordinary_geometric_pad_readings(self):
        lg, participant = leg(), pad()
        crossing = pad("crossing-pad", x=10.0, z=0.0, datum=40.0)
        geometric = self.solve([lg], [participant, crossing])
        self.assertEqual(
            geometric["legs"][lg["id"]]["shares_ground_with"], [crossing["id"]])
        reading = arrival(lg, participant)
        lg["arrival_readings"] = [reading]
        result = self.solve([lg], [participant, crossing])
        self.assertEqual(result["refused"], [])
        self.assertEqual(result["legs"][lg["id"]]["shares_ground_with"],
                         sorted([participant["id"], crossing["id"]]))
        self.assert_legal(result, lg, reading)

    def test_missing_declared_pad_and_invalid_metadata_are_not_ignored(self):
        lg, participant = leg(), pad()
        reading = arrival(lg, participant)
        cases = [("missing pad", {"pad": "missing-pad"}),
                 ("wrong datum", {"datum": participant["datum_y"] + 0.1}),
                 ("negative allowance", {"allow": -0.1}),
                 ("unresolved station", {"station": -1}),
                 ("fractional station", {"station": 0.5}),
                 ("boolean station", {"station": False}),
                 ("interior station", {"station": 1, "x": 10.0}),
                 ("outside station", {"station": 3}),
                 ("wrong coordinates", {"x": reading["x"] + 1e-7})]
        cases += [(f"nonfinite {key}", {key: value})
                  for key in ("datum", "allow", "x", "z")
                  for value in (math.inf, -math.inf, math.nan)]
        for name, changes in cases:
            with self.subTest(case=name, changes=changes):
                candidate = deepcopy(lg)
                candidate["arrival_readings"] = [dict(reading, **changes)]
                with self.assertRaises(ValueError):
                    self.solve([candidate], [participant])


if __name__ == "__main__":
    unittest.main()
