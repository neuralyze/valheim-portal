"""Checkpoint crash recovery and real temporary-file heartbeat behavior."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shore_progress as P


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def timestamp(self):
        return f"time-{self.now}"


class ShoreProgressTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "progress.json"
        self.basis = {"requirement_ids": ["pier", "ferry"],
                      "fields": "field-hash", "source": "code-hash",
                      "geometry": {"step": 2.0, "threshold": 0.125}}
        self.clock = Clock()
        self.stdout = io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.enterContext(patch.object(P.time, "monotonic", self.clock.monotonic))
        self.enterContext(patch.object(P.ShoreProgress, "_timestamp", self.clock.timestamp))

    def progress(self):
        return P.ShoreProgress(self.path, self.basis, interval_seconds=10.0)

    def document(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_completed_prefix_survives_partial_search_and_pair_is_one_commit(self):
        progress = self.progress()
        pier = {"id": "pier", "root": [1.25, 2.5]}
        progress.complete_requirement("pier", pier)
        pier["root"][0] = 999.0
        self.clock.now = 1.0
        progress({"requirement_id": "ferry", "root_index": 37,
                  "root_total": 120, "length": 64, "bearing": 285,
                  "counters": {"water": 920023, "approach": 27363},
                  "elapsed_seconds": 123.5})
        resumed = self.progress()
        self.assertEqual(resumed.load_completed(),
                         {"pier": {"id": "pier", "root": [1.25, 2.5]}})
        detached = resumed.load_completed()
        detached["pier"]["root"][0] = -1.0
        self.assertEqual(resumed.load_completed()["pier"]["root"][0], 1.25)
        pair = [{"id": "ferry-a", "root": [3, 4]},
                {"id": "ferry-b", "root": [5, 6]}]
        resumed.complete_requirement("ferry", pair)
        recovered = self.progress().load_completed()
        self.assertEqual(list(recovered), ["pier", "ferry"])
        self.assertEqual(recovered["ferry"], pair)

    def test_interrupted_fsync_keeps_previous_file_and_in_memory_prefix(self):
        progress = self.progress()
        progress.complete_requirement("pier", {"id": "pier"})
        before = self.path.read_bytes()
        with patch.object(P.os, "fsync", side_effect=OSError("interrupted write")):
            with self.assertRaises(OSError):
                progress.complete_requirement("ferry", {"ends": ["a", "b"]})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(progress.load_completed()), ["pier"])
        self.assertEqual(list(self.progress().load_completed()), ["pier"])
        self.assertEqual([path.name for path in self.path.parent.iterdir()],
                         ["progress.json"])
        progress.complete_requirement("ferry", {"ends": ["a", "b"]})
        self.assertEqual(list(self.progress().load_completed()), ["pier", "ferry"])

    def test_interrupted_replace_and_serialization_leave_previous_good_file(self):
        progress = self.progress()
        progress.complete_requirement("pier", {"id": "pier"})
        before = self.path.read_bytes()
        with patch.object(P.os, "replace", side_effect=OSError("interrupted replace")):
            with self.assertRaises(OSError):
                progress.complete_requirement("ferry", {"ends": ["a", "b"]})
        with self.assertRaises(ValueError):
            progress.complete_requirement("ferry", {"depth": float("nan")})
        with self.assertRaises(ValueError):
            progress.complete_requirement("ferry", {"executable": object()})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(progress.load_completed()), ["pier"])
        self.assertEqual([path.name for path in self.path.parent.iterdir()],
                         ["progress.json"])

    def test_file_is_fsynced_before_replace(self):
        progress = self.progress()
        calls = []
        real_sync, real_replace = P.os.fsync, P.os.replace

        def sync(fd):
            calls.append("fsync")
            return real_sync(fd)

        def replace(source, destination):
            calls.append("replace")
            self.assertEqual(json.loads(Path(source).read_text())["completed"][0]["id"],
                             "pier")
            return real_replace(source, destination)

        with patch.object(P.os, "fsync", sync), patch.object(P.os, "replace", replace):
            progress.complete_requirement("pier", {"id": "pier"})
        self.assertEqual(calls, ["fsync", "replace", "fsync"])
        self.assertEqual(list(self.progress().load_completed()), ["pier"])

    def test_every_basis_difference_invalidates_reuse_without_overwriting(self):
        progress = self.progress()
        progress.complete_requirement("pier", {"id": "pier"})
        before = self.path.read_bytes()
        for key, changed in (("fields", "new-field-hash"),
                             ("source", "new-code-hash"),
                             ("geometry", {"step": 2, "threshold": 0.125}),
                             ("requirement_ids", ["ferry", "pier"])):
            with self.subTest(key=key):
                basis = {**self.basis, key: changed}
                rejected = P.ShoreProgress(self.path, basis)
                self.assertEqual(rejected.load_completed(), {})
                self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("checkpoint ignored", self.stdout.getvalue())
        self.assertEqual(list(self.progress().load_completed()), ["pier"])

    def test_out_of_order_and_duplicate_completions_never_commit(self):
        progress = self.progress()
        with self.assertRaises(ValueError):
            progress.complete_requirement("ferry", {"ends": ["a", "b"]})
        self.assertFalse(self.path.exists())
        progress.complete_requirement("pier", {"id": "pier"})
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            progress.complete_requirement("pier", {"id": "pier-again"})
        self.assertEqual(self.path.read_bytes(), before)
        document = self.document()
        document["completed"] = [{"id": "ferry", "site": {"ends": ["a", "b"]}}]
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(self.progress().load_completed(), {})

    def test_malformed_and_partial_checkpoints_do_not_resume(self):
        for text in ('{"version":1,"completed":[',
                     '{"version":1,"version":1}',
                     json.dumps({"version": 1, "basis": self.basis,
                                 "completed": [{"id": "pier", "site": {}}]})):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                self.assertEqual(self.progress().load_completed(), {})
        self.path.write_bytes(b"\xff\xfe")
        self.assertEqual(self.progress().load_completed(), {})

    def test_throttled_heartbeats_distinguish_stall_from_actual_counter_increase(self):
        progress = self.progress()
        counters = {"water": 100, "approach": 3}
        progress({"requirement_id": "pier", "root_index": 4, "root_total": 20,
                  "length": 64, "bearing": 285, "counters": counters,
                  "elapsed_seconds": 0})
        initial = self.document()
        initial_bytes = self.path.read_bytes()
        self.clock.now = 1.0
        progress({"elapsed_seconds": 1})
        self.assertEqual(self.path.read_bytes(), initial_bytes)
        self.clock.now = 10.0
        progress({"elapsed_seconds": 10})
        stalled = self.document()["progress"]
        self.assertEqual(stalled["work_status"], "unchanged")
        self.assertEqual(stalled["last_advancement_at"],
                         initial["progress"]["last_advancement_at"])
        self.assertEqual(stalled["last_counter_advancement_at"],
                         initial["progress"]["last_counter_advancement_at"])
        self.clock.now = 11.0
        counters["water"] += 1020
        progress({"counters": counters, "elapsed_seconds": 11})
        self.assertEqual(self.document()["progress"], stalled)
        self.clock.now = 20.0
        progress({"elapsed_seconds": 20})
        increased = self.document()["progress"]
        self.assertEqual(increased["work_status"], "advanced")
        self.assertEqual(increased["event"]["counters"],
                         {"water": 1120, "approach": 3})
        self.assertEqual(increased["last_counter_advancement_at"], "time-11.0")
        self.assertEqual(increased["event"]["elapsed_seconds"], 20)
        self.assertIn("unchanged", self.stdout.getvalue())
        self.assertIn("water=1120", self.stdout.getvalue())

    def test_position_advancement_is_distinct_from_counter_advancement(self):
        progress = self.progress()
        progress({"requirement_id": "pier", "root_index": 1,
                  "counters": {"water": 5}})
        self.clock.now = 10.0
        progress({"root_index": 2, "counters": {"water": 5}})
        state = self.document()["progress"]
        self.assertEqual(state["work_status"], "advanced")
        self.assertEqual(state["last_advancement_at"], "time-10.0")
        self.assertEqual(state["last_counter_advancement_at"], "time-0.0")

    def test_initial_transition_and_final_events_bypass_throttle(self):
        progress = self.progress()
        progress({"requirement_id": "pier", "counters": {"rays": 1}})
        self.assertTrue(self.path.is_file())
        self.clock.now = 0.1
        progress({"requirement_id": "ferry", "counters": {"rays": 2}})
        self.assertEqual(self.document()["progress"]["event"]["requirement_id"], "ferry")
        self.clock.now = 0.2
        progress({"phase": "failed", "elapsed_seconds": 0.2})
        self.assertEqual(self.document()["progress"]["event"]["phase"], "failed")
        self.assertEqual(self.document()["completed"], [])
        self.assertEqual(self.progress().load_completed(), {})

    def test_requirement_transition_does_not_report_previous_search_coordinates(self):
        progress = self.progress()
        progress({"requirement_id": "pier", "root_index": 19, "root_total": 20,
                  "length": 94, "bearing": 225, "phase": "search",
                  "counters": {"rays": 72}})
        self.clock.now = 0.1
        progress({"requirement_id": "ferry"})
        event = self.document()["progress"]["event"]
        self.assertEqual(event["requirement_id"], "ferry")
        self.assertEqual(event["counters"], {"rays": 72})
        for key in ("root_index", "root_total", "length", "bearing", "phase"):
            self.assertNotIn(key, event)

    def test_elapsed_time_alone_is_not_invented_work_advancement(self):
        progress = self.progress()
        progress({"elapsed_seconds": 40})
        state = self.document()["progress"]
        self.assertEqual(state["work_status"], "unchanged")
        self.assertIsNone(state["last_advancement_at"])
        self.assertIsNone(state["last_counter_advancement_at"])

    def test_event_cannot_supply_authoritative_basis_or_completed_results(self):
        progress = self.progress()
        progress({"basis": {"forged": True},
                  "completed": [{"id": "pier", "site": {"forged": True}}],
                  "version": 999, "phase": "done"})
        document = self.document()
        self.assertEqual(document["basis"], self.basis)
        self.assertEqual(document["completed"], [])
        self.assertEqual(document["version"], 1)
        self.assertEqual(self.progress().load_completed(), {})


if __name__ == "__main__":
    unittest.main()
