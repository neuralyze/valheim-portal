"""Real native producer constraints survive exact transport and joint refresh."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from scipy.sparse import csr_matrix, vstack

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import profile_contract as C
import route as R
from shore_road_preview import NativeShorePreview


class NativeProfileModelTests(unittest.TestCase):
    def setUp(self):
        self.native = NativeShorePreview()._prepare()

    def produce(self, points, *, identifier="native", height=35., held=None,
                ceiling=None, trim=None):
        points = np.asarray(points, dtype=float)
        field = self.native._ww_field(lambda x, z: np.full_like(x, height))
        nodes = {
            "a": dict(xz=tuple(points[0]), kind="extremity", island="q", patch="q"),
            "b": dict(xz=tuple(points[-1]), kind="extremity", island="q", patch="q")}
        if held is not None:
            nodes["a"].update(kind="abutment", level_y=held)
        edge = dict(id=identifier, a="a", b="b", cls="spur", pri=2)
        joint = {}
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.native.specmod, "NODES", nodes))
            stack.enter_context(patch.object(self.native, "_PAD_CLAIMS", []))
            stack.enter_context(patch.object(self.native, "_PAD_DATUMS", {}))
            stack.enter_context(patch.object(self.native, "_PLAN_PAD_KEEPOUTS", None))
            record = self.native.build_segment(
                field, edge, self.native._ww_corridor(field), [],
                fixed_pts=points.tolist(), census=None, joint=joint,
                ceiling=ceiling, trim=trim)
        self.assertEqual(record["native_profile_status"]["verdict"], "COMPLETE")
        return record, joint

    def straight(self):
        return np.column_stack((np.arange(0., 62., 2.) + .3141592653589793,
                                np.full(31, .1234567890123456)))

    def solve(self, model, y0, extra_column=None, extra_lower=None):
        inputs = model["inputs"]
        matrix, lower, upper = C.native_profile_readings(model)
        if extra_column is not None:
            row = csr_matrix(([1.], ([0], [extra_column])), shape=(1, len(y0)))
            matrix = vstack((matrix, row), format="csr")
            lower = np.r_[lower, extra_lower]
            upper = np.r_[upper, inputs["hi"][extra_column]]
        return R._exact_reading_profile(
            y0, inputs["lo"], inputs["hi"],
            [(0, len(y0), inputs["rung"] * inputs["s"])], matrix, lower, upper)

    def test_lossless_emitted_geometry_held_bank_and_total_grade_inventory(self):
        record, joint = self.produce(self.straight(), held=30.6)
        packet = json.loads(json.dumps(record, allow_nan=False))
        model = C.read_native_profile_model(packet)
        inputs = model["inputs"]
        np.testing.assert_array_equal(packet["nodes"], self.straight())
        np.testing.assert_array_equal(inputs["s"], R._writer().node_arcs(
            record["nodes"], record["is_bridge"]))
        for key in ("lo", "hi", "held", "free", "tw_lo", "tw_hi"):
            np.testing.assert_array_equal(inputs[key], joint[key])
        self.assertTrue(inputs["held"][:4].all())
        np.testing.assert_array_equal(inputs["lo"][:4], [30.6] * 4)
        np.testing.assert_array_equal(inputs["hi"][:4], [30.6] * 4)
        self.assertEqual(inputs["rung"], .25)
        y = self.solve(model, np.asarray(packet["profile_y"]))
        self.assertIsNotNone(y)
        np.testing.assert_allclose(y[:4], 30.6, rtol=0., atol=1e-12)
        self.assertLessEqual(np.max(abs(np.diff(y)) / np.diff(inputs["s"])), .25 + 1e-9)
        # A legal local box value is still globally impossible from the held
        # 30.6 m run at the ORIGINAL total grade. It is not a shift-hat test.
        self.assertLess(inputs["lo"][4], inputs["hi"][4])
        self.assertIsNone(self.solve(model, np.asarray(packet["profile_y"]),
                                     extra_column=4, extra_lower=inputs["hi"][4] - .1))

    def test_real_self_overlap_inventory_constrains_interpolated_feet(self):
        path = np.array([[0., 0.], [40., 0.], [40., 8.], [0., 8.]])
        points, _ = R.resample(path, 2.)
        record, _ = self.produce(points)
        model = C.read_native_profile_model(record)
        self.assertGreater(len(model["self_lattice"]["edges"]["s1"]), 0)
        inputs = model["inputs"]
        proposal = 31. + .10 * inputs["s"]
        self.assertTrue(np.all(proposal >= inputs["lo"]))
        self.assertTrue(np.all(proposal <= inputs["hi"]))
        self.assertTrue(np.all(abs(np.diff(proposal)) <= inputs["rung"]
                               * np.diff(inputs["s"]) + 1e-9))
        self.assertTrue(R.self_adjacency_violations(proposal, model["self_lattice"]))
        illegal = copy.deepcopy(record)
        illegal["profile_y"] = proposal.tolist()
        with self.assertRaises(C.NativeProfileModelError):
            C.bind_native_profile_model(illegal, model)
        legal = self.solve(model, proposal)
        self.assertIsNotNone(legal)
        self.assertFalse(R.self_adjacency_violations(legal, model["self_lattice"]))
        record["profile_y"] = legal.tolist()
        C.bind_native_profile_model(record, model)
        C.read_native_profile_model(record)

    def test_complete_illegal_profile_is_physical_refusal_not_missing_inputs(self):
        points, _ = R.resample(
            np.array([[0., 0.], [40., 0.], [40., 8.], [0., 8.]]), 2.)
        record, joint = self.produce(points)
        original = C.read_native_profile_model(record)
        proposal = 31. + .10 * original["inputs"]["s"]
        record["profile_y"] = proposal.tolist()
        self.assertIsNone(C.refresh_native_profile_model(record, joint))
        status = record["native_profile_status"]
        self.assertEqual(status["verdict"], "REFUSED")
        self.assertGreater(status["constraint"]["excess_m"], 0.)
        self.assertNotIn("native_profile_model", record)
        self.assertEqual(self.native.plan_refusal(
            record, [], "fixture", fld=None, coverage=False),
            "native_profile_refusal")

    def test_incomplete_native_inputs_remain_a_replan_prerequisite(self):
        record, joint = self.produce(self.straight())
        del joint["cand"]
        self.assertIsNone(C.refresh_native_profile_model(record, joint))
        self.assertEqual(record["native_profile_status"]["verdict"], "UNPROVEN")
        self.assertEqual(
            self.native.native_profile_prerequisite(record)["verdict"],
            "PREREQUISITE")

    def test_incomplete_lattice_precedes_numeric_profile_refusal(self):
        points, _ = R.resample(
            np.array([[0., 0.], [40., 0.], [40., 8.], [0., 8.]]), 2.)
        record, joint = self.produce(points)
        self.assertGreater(len(joint["cand"]["edges"]["s1"]), 0)
        for key in joint["cand"]["edges"]:
            joint["cand"]["edges"][key] = []
        for key in ("near1", "near2", "dsum", "allow_e"):
            joint["cand"][key] = []
        station = len(record["profile_y"]) // 2
        record["profile_y"][station] = float(joint["hi"][station]) + 1.
        self.assertIsNone(C.refresh_native_profile_model(record, joint))
        self.assertEqual(record["native_profile_status"]["verdict"], "UNPROVEN")
        self.assertEqual(
            self.native.native_profile_prerequisite(record)["verdict"],
            "PREREQUISITE")
        self.assertNotIn("native_profile_model", record)

    def test_truncated_self_inventory_is_a_prerequisite_before_writer_admission(self):
        points, _ = R.resample(
            np.array([[0., 0.], [40., 0.], [40., 8.], [0., 8.]]), 2.)
        record, _ = self.produce(points)
        model = record["native_profile_model"]
        self.assertGreater(len(model["self_lattice"]["edges"]["s1"]), 0)
        # A partial exporter can hash its own incomplete output consistently.
        # Integrity alone must not certify that omitted native edges are clear.
        for key in model["self_lattice"]["edges"]:
            model["self_lattice"]["edges"][key] = []
        for key in ("near1", "near2", "dsum", "allow_e"):
            model["self_lattice"][key] = []
        model["sha256"] = C._digest(
            {key: value for key, value in model.items() if key != "sha256"})
        with self.assertRaisesRegex(C.NativeProfileModelError, "self-lattice inventory"):
            C.read_native_profile_model(record)
        prerequisite = self.native.native_profile_prerequisite(record)
        self.assertEqual(prerequisite["verdict"], "PREREQUISITE")
        self.assertEqual(record["native_profile_status"]["verdict"], "UNPROVEN")
        self.assertNotIn("native_profile_model", record)

    def test_joint_refresh_retains_actual_final_shared_readings_and_original_bounds(self):
        points = self.straight()
        first, jfirst = self.produce(points, identifier="first", height=35.123456789)
        second, jsecond = self.produce(points, identifier="second", height=37.234567891)
        before = copy.deepcopy(first["native_profile_model"])
        result = self.native.apply_junction_datums(
            [first, second], {"first": jfirst, "second": jsecond}, [])
        self.assertFalse(result["refused"])
        self.assertTrue(any(v["stations_moved"] for v in result["legs"].values()))
        self.assertNotEqual(first["native_profile_model"]["sha256"], before["sha256"])
        for record, joint in ((first, jfirst), (second, jsecond)):
            model = C.read_native_profile_model(json.loads(json.dumps(record, allow_nan=False)))
            np.testing.assert_array_equal(model["inputs"]["lo"], joint["lo"])
            rows = model["readings"]
            self.assertTrue(any(row["kind"] == "joint_original_bound" for row in rows))
            locks = [row for row in rows if row["kind"] == "published_joint_datum"]
            self.assertTrue(locks)
            self.assertTrue(all(row["lower_y"] == row["upper_y"] for row in locks))
            legal = self.solve(model, np.asarray(record["profile_y"]) + .1)
            self.assertIsNotNone(legal)
            for row in locks:
                value = sum(legal[k] * w for k, w in zip(row["columns"], row["weights"]))
                self.assertAlmostEqual(value, row["lower_y"], places=8)

    def test_fixed_refit_water_ceiling_and_trim_export_the_new_inputs(self):
        points = self.straight()
        original, _ = self.produce(points)
        capped, joint = self.produce(points, ceiling={15: 34.876543219})
        model = C.read_native_profile_model(capped)
        self.assertEqual(model["inputs"]["hi"][15], 34.876543219)
        self.assertEqual(model["physical_basis"]["walked_water_ceiling_inputs"],
                         {"15": 34.876543219})
        self.assertLessEqual(capped["profile_y"][15], 34.876543219 + 1e-9)
        self.assertNotEqual(original["native_profile_model"]["sha256"],
                            capped["native_profile_model"]["sha256"])
        trimmed, _ = self.produce(points, trim={"a": 2})
        C.read_native_profile_model(trimmed)
        np.testing.assert_array_equal(trimmed["nodes"], points[2:])
        self.assertEqual(len(trimmed["native_profile_model"]["inputs"]["lo"]), len(points) - 2)
        self.assertEqual(len(model["inputs"]["lo"]), len(joint["lo"]))

    def test_no_stale_model_survives_any_physical_binding_change(self):
        record, _ = self.produce(self.straight())
        for field, replacement in (
                ("nodes", [[x + .00001, z] for x, z in record["nodes"]]),
                ("profile_y", [y + .00001 for y in record["profile_y"]]),
                ("width_m", record["width_m"] + .00001),
                ("cls", "mountain"),
                ("terminal_caps", [False, True]),
                ("is_bridge", [True] + record["is_bridge"][1:])):
            changed = copy.deepcopy(record)
            changed[field] = replacement
            with self.subTest(field=field), self.assertRaises(C.NativeProfileModelError):
                C.read_native_profile_model(changed)

    def test_missing_native_basis_invalidates_previous_complete_model(self):
        record, joint = self.produce(self.straight())
        joint.pop("native_basis")
        self.assertIsNone(C.refresh_native_profile_model(record, joint))
        self.assertNotIn("native_profile_model", record)
        self.assertEqual(record["native_profile_status"]["verdict"], "UNPROVEN")
        with self.assertRaises(C.NativeProfileModelError):
            C.read_native_profile_model(record)

    def test_only_actual_unbounded_native_intervals_encode_as_null(self):
        _, joint = self.produce(self.straight())
        lower = np.full(len(joint["s"]), -np.inf)
        upper = np.full(len(joint["s"]), np.inf)
        fit = R.fit_profile(joint["t"], joint["s"], .12, lo=lower, hi=upper)
        self.assertFalse(fit["infeasible_spans"])
        np.testing.assert_array_equal(fit["y"], joint["t"])
        # Test the lossless numeric transport, not invented bridge ownership
        # or a made-up authoritative physical segment.
        packet = C._encode_bounds({"inputs": {
            "lo": fit["lo"].tolist(), "hi": fit["hi"].tolist()}})
        packet = json.loads(json.dumps(packet, allow_nan=False))
        self.assertTrue(all(value is None for value in packet["inputs"]["lo"]))
        self.assertTrue(all(value is None for value in packet["inputs"]["hi"]))
        self.assertEqual(packet["inputs"]["unbounded_lower"], list(range(len(lower))))
        self.assertEqual(packet["inputs"]["unbounded_upper"], list(range(len(upper))))
        with self.assertRaises(C.NativeProfileModelError):
            C.read_native_profile_model({"native_profile_model": packet})

    def test_feedback_rebinding_cannot_replace_original_constraints(self):
        record, _ = self.produce(self.straight())
        model = C.read_native_profile_model(record)
        model["inputs"]["hi"][10] += 1.
        with self.assertRaises(C.NativeProfileModelError):
            C.bind_native_profile_model(record, model)


if __name__ == "__main__":
    unittest.main()
