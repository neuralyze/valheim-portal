"""A clustered census must not lose the ground under individual objects."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import precensus as C
import ribbon as R
import network as N


class ObservedGroundTests(unittest.TestCase):
    @staticmethod
    def census(objects, *, cell=(0, 0)):
        body = C.Body("cluster", 10.0, 6.0, 50.0, 6.0, 2, 2,
                      {"wood_floor": 2}, "fixture", "2026-01-01T00:00:00Z")
        return C.Census("FIX", "FIX", "2026-01-01T00:00:00Z", "fixture",
                        16.0, [cell], [body], observed_objects=objects)

    @staticmethod
    def object(x, z, prefab="wood_floor"):
        return {"prefab": prefab, "x": x, "y": 50.0, "z": z}

    @staticmethod
    def stamp(positions):
        return R._rasterise([(0.0, 0.0), (20.0, 0.0)], 55.0,
                            lambda x, z: 50.0, placed=positions)

    def test_merged_saved_census_freezes_both_observed_objects_not_centroid(self):
        a = self.census([self.object(4.0, 6.0)])
        b = self.census([self.object(16.0, 6.0)], cell=(1, 0))
        merged = C.Census.merge([a, b], instrument="merged fixture")
        restored = C.Census.from_json(json.loads(json.dumps(merged.to_json())))
        protected = self.stamp(restored.placed_positions())
        centroid_only = self.stamp([(10.0, 6.0)])
        for x in (4.0, 16.0):
            with self.subTest(x=x):
                self.assertEqual(R.delta_at(protected["_comps"], x, 6.0), 0.0)
                self.assertGreater(R.delta_at(centroid_only["_comps"], x, 6.0), 1.0)

    def test_one_unobserved_part_cannot_become_measured_empty_on_merge(self):
        complete = self.census([])
        unknown = self.census(None, cell=(1, 0))
        for parts in ([complete, unknown], [unknown, complete]):
            with self.subTest(order=[p.cells for p in parts]):
                merged = C.Census.merge(parts, instrument="mixed fixture")
                with self.assertRaisesRegex(SystemExit, "observed object positions"):
                    merged.placed_positions()

    def test_measured_empty_and_unrecorded_population_remain_distinct(self):
        known = C.Census.from_json(self.census([]).to_json())
        unknown_doc = self.census(None).to_json()
        unknown_doc.pop("observed_objects")
        unknown = C.Census.from_json(unknown_doc)
        self.assertEqual(known.placed_positions(), [])
        with self.assertRaisesRegex(SystemExit, "re-survey"):
            unknown.placed_positions()

    def test_freeze_population_uses_existing_prefab_and_engine_rules(self):
        rows = [self.object(4.0, 6.0), self.object(16.0, 6.0, "_ZoneCtrl"),
                self.object(8.0, 6.0, "Beech_small1"),
                self.object(12.0, 6.0, "UnknownSeatedModPiece")]
        census = self.census(rows)
        positions = census.placed_positions()
        protected = self.stamp(positions)
        unprotected = self.stamp([])
        for x in (4.0, 12.0):
            with self.subTest(protected=x):
                self.assertEqual(R.delta_at(protected["_comps"], x, 6.0), 0.0)
        for x in (8.0, 16.0):
            with self.subTest(not_seated=x):
                self.assertGreater(R.delta_at(unprotected["_comps"], x, 6.0), 1.0)
                self.assertGreater(R.delta_at(protected["_comps"], x, 6.0), 1.0)

    def test_nonfinite_protected_position_is_not_clear_ground(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "non-finite"):
                    self.census([self.object(value, 6.0)]).placed_positions()

    def test_frozen_save_producer_preserves_actual_observed_ground(self):
        rows = [self.object(0.0, 0.0, "_ZoneCtrl"),
                self.object(4.0, 6.0), self.object(16.0, 6.0)]
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "zdos.tsv"
            source.write_text("# independently supplied complete fixture rows\n")
            census = C.census_from_save(
                source, [(0.0, 0.0), (20.0, 0.0)], rows=rows,
                ground_of=lambda x, z: 50.0, world="FIX", seed="FIX",
                fingerprint="fixture-frozen-save")
        protected = self.stamp(census.placed_positions())
        for x in (4.0, 16.0):
            with self.subTest(x=x):
                self.assertEqual(R.delta_at(protected["_comps"], x, 6.0), 0.0)

    def test_native_plan_refuses_cliffs_its_observed_body_guard_causes(self):
        captured = {}
        original = R.stamp

        def observing_stamp(*args, **kwargs):
            captured.update(segment=args[0], patches=args[1])
            return original(*args, **kwargs)

        with patch.object(R, "stamp", side_effect=observing_stamp):
            self.stamp([])
        census = self.census([self.object(4.0, 6.0)])
        with patch.object(N, "_PLAN_PATCHES",
                          lambda field, zones: captured["patches"]), \
                patch.object(N, "_PLAN_PAD_KEEPOUTS", None), \
                patch.object(N, "_PLAN_KEEPOUTS", None), \
                patch.object(N, "_PLAN_PLACED_OBJECTS", None, create=True):
            unguarded = N._writer_water_once(captured["segment"], None)
            N._PLAN_PLACED_OBJECTS = census.placed_positions()
            guarded = N._writer_water_once(captured["segment"], None)
        self.assertEqual(unguarded["verdict"], "CLEAR")
        self.assertEqual(guarded["verdict"], "REFUSED")
        self.assertGreater(guarded["steps"], 0)
        self.assertEqual(guarded["drained"], 0)


if __name__ == "__main__":
    unittest.main()
