#!/usr/bin/env python3
"""Tests for the blueprint floor datum -- `jumpstart/blueprints/base_geometry.py`.

The defect these cover, reported from live play on Ulfsland: every blueprint
structure was placed ~1.5-3 m too high, floating above the pad that had been
flattened for it, while loose props spawned at the same pad height sat on the
ground correctly.

Cause: `to_rcon_plan.py --align ground-center` computed `dy = -min(pivot Y)` and
called that "drop the lowest piece onto the pad". A pivot is not a surface --
MEASURED from the game's own colliders, `stone_floor_2x2` is a 2 x 2 x 1 m solid
whose pivot sits at mid-thickness, so its walkable top is pivot + 0.5 and its
underside pivot - 0.5. Worse, 99 of the 157 non-empty `.blueprint` bodies in the
catalogued corpus are already normalised so that `min(pivot Y) == 0`, which made
that shift EXACTLY ZERO on the majority of the corpus: a computation that
confidently answered a question it was not measuring.

`test_shift_is_not_a_no_op_on_a_normalised_body` is the guard against that
returning: it asserts the shift is non-zero for a body whose floor is not at
local Y = 0 but whose min pivot Y is, which is the exact shape the old rule got
wrong.

Geometry comes from `jumpstart/blueprints/data/piece_geometry.json`, dumped from
a sandbox dedicated server by `PieceGeometry.cs`; the tests that assert specific
prefab dimensions exist so a bad regeneration of that file is caught here rather
than in a world.
"""

import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BLUEPRINTS = TOOLS / "jumpstart" / "blueprints"
sys.path.insert(0, str(BLUEPRINTS))

import base_geometry as bg  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402

HEADER = """#Name:{name}
#Creator:test
#Description:fixture
#Category:test
#Pieces
"""


def row(prefab: str, x: float, y: float, z: float, rot=(0.0, 0.0, 0.0, 1.0)) -> str:
    rx, ry, rz, rw = rot
    return f"{prefab};;{x};{y};{z};{rx};{ry};{rz};{rw};;1;1;1;"


def blueprint(tmp: Path, name: str, rows: list[str]) -> Path:
    path = tmp / f"{name}.blueprint"
    path.write_text(HEADER.format(name=name) + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def old_rule_dy(objs) -> float:
    """`--align ground-center` as it was: minus the lowest PIVOT."""
    return -min(o.pos[1] for o in objs)


class MeasuredGeometryContract(unittest.TestCase):
    """The prefab dimensions the datum rule leans on, asserted against the
    committed collider dump so a bad regeneration fails here."""

    @classmethod
    def setUpClass(cls):
        cls.g = bg.geometry()

    def test_stone_floor_pivot_sits_at_mid_thickness(self):
        span = self.g.y_span("stone_floor_2x2", (0, 0, 0, 1), (1, 1, 1))
        self.assertEqual((round(span[0], 3), round(span[1], 3)), (-0.5, 0.5))

    def test_thin_wood_floor_is_not_half_a_metre_thick(self):
        span = self.g.y_span("wood_floor", (0, 0, 0, 1), (1, 1, 1))
        self.assertAlmostEqual(span[1], 0.0969, places=3)
        self.assertAlmostEqual(span[0], -0.0329, places=3)

    def test_ground_resting_prefabs_have_their_solid_start_at_the_pivot(self):
        # This is why a furnishing's Y reads the surface it stands on, which is
        # what makes the station guard and the props check meaningful.
        for prefab in ("piece_workbench", "forge", "hearth", "piece_chest_wood",
                       "smelter", "charcoal_kiln"):
            with self.subTest(prefab=prefab):
                box = self.g.local_aabb(prefab)
                self.assertIsNotNone(box, prefab)
                self.assertLess(abs(box[1]), bg.PIVOT_AT_BASE_TOL_M, prefab)

    def test_rotation_is_applied_to_the_collider_not_just_the_pivot(self):
        # 90 degrees about X turns the 2 m Z extent of stone_floor_2x2 into its Y
        # extent. An implementation that only read an unrotated AABB, or that
        # ignored rotation entirely, returns (-0.5, 0.5) here.
        h = math.sin(math.radians(45.0))
        span = self.g.y_span("stone_floor_2x2", (h, 0.0, 0.0, h), (1, 1, 1))
        self.assertEqual((round(span[0], 3), round(span[1], 3)), (-1.0, 1.0))

    def test_world_decor_is_distinguished_from_build_pieces(self):
        self.assertTrue(self.g.is_build_piece("stone_floor_2x2"))
        self.assertFalse(self.g.is_build_piece("Rock_4"))

    def test_a_brazier_named_floor_is_not_a_floor(self):
        # Name-only classification admits `piece_brazierfloor01`, a 4 x 4 x 4 m
        # brazier; the measured slab test rejects it.
        self.assertFalse(self.g.is_floor("piece_brazierfloor01"))
        self.assertTrue(self.g.is_floor("stone_floor_2x2"))


class FloorDatumCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = bg.geometry()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_origin_above_the_floor_places_the_floor_on_the_pad(self):
        # Floor slab top at local -1.903; a wall course sunk below it takes the
        # minimum pivot down to -3.0. The old rule lifts the whole body by 3.0,
        # putting the walkable floor 1.097 m above the pad.
        path = blueprint(self.tmp, "origin_above_floor", [
            row("wood_floor", 0, -2.0, 0),
            row("wood_floor", 2, -2.0, 0),
            row("stone_wall_1x1", 0, -3.0, 0),
            row("wood_wall_half", 0, -1.0, 0),
        ])
        objs = read_objects(path)
        datum = bg.floor_datum(objs, self.g)
        self.assertTrue(datum.placeable)
        self.assertAlmostEqual(datum.base_y, -1.903, places=3)

        pad = 70.91
        self.assertAlmostEqual(datum.place_y(pad), pad + 1.903, places=3)
        # the floor's walkable surface lands exactly on the pad
        floor_top = datum.place_y(pad) + (-2.0) + self.g.y_span(
            "wood_floor", (0, 0, 0, 1), (1, 1, 1))[1]
        self.assertAlmostEqual(floor_top, pad, places=3)
        # and the old rule did not
        old_top = (pad + old_rule_dy(objs)) + (-2.0) + 0.0969
        self.assertAlmostEqual(old_top - pad, 1.0969, places=3)

    def test_origin_already_at_the_floor_needs_no_shift(self):
        # Slab pivot at -0.5 puts its walkable top exactly on local 0, so the
        # datum is 0 and the placement Y is the pad itself. The old rule still
        # lifts the body 0.5 m, because -min(pivot) is 0.5 here.
        path = blueprint(self.tmp, "origin_at_floor", [
            row("stone_floor_2x2", 0, -0.5, 0),
            row("stone_floor_2x2", 2, -0.5, 0),
            row("stone_wall_2x1", 0, 0.5, 0),
        ])
        objs = read_objects(path)
        datum = bg.floor_datum(objs, self.g)
        self.assertEqual(round(datum.base_y, 6), 0.0)
        self.assertAlmostEqual(datum.place_y(70.91), 70.91, places=6)
        self.assertAlmostEqual(old_rule_dy(objs), 0.5, places=6)

    def test_a_buried_foundation_stays_buried(self):
        # A pole sunk into the terrain is a pole doing its job. The datum is the
        # floor above it, so the pole's solid still ends up BELOW the pad; a rule
        # that aligned the lowest solid instead would lift it clear and float the
        # floor by 2.5 m.
        path = blueprint(self.tmp, "buried_foundation", [
            row("stone_floor_2x2", 0, 0.5, 0),
            row("stone_floor_2x2", 2, 0.5, 0),
            row("wood_pole", 0, -1.0, 0),
            row("wood_pole", 2, -1.0, 0),
            row("piece_workbench", 1, 1.0, 0),
        ])
        objs = read_objects(path)
        datum = bg.floor_datum(objs, self.g)
        self.assertAlmostEqual(datum.base_y, 1.0, places=6)
        self.assertAlmostEqual(datum.bottom_solid, -1.5, places=6)
        self.assertAlmostEqual(datum.support_bottom, -1.5, places=6)

        pad = 70.91
        origin_y = datum.place_y(pad)
        self.assertAlmostEqual(origin_y, pad - 1.0, places=6)
        pole_bottom = origin_y + (-1.0) + self.g.y_span(
            "wood_pole", (0, 0, 0, 1), (1, 1, 1))[0]
        self.assertAlmostEqual(pole_bottom, pad - 2.5, places=6)
        self.assertLess(pole_bottom, pad, "the sunk pole must stay sunk")
        # aligning the lowest solid instead would have floated the floor
        self.assertAlmostEqual(datum.base_y - datum.bottom_solid, 2.5, places=6)

    def test_shift_is_not_a_no_op_on_a_normalised_body(self):
        # The regression this whole module exists for. This body is normalised
        # the way 99 of the 157 non-empty corpus bodies are -- min pivot Y is
        # exactly 0 -- so the OLD rule's shift is exactly zero and does nothing,
        # while the floor it should have aligned is 1.5 m up.
        path = blueprint(self.tmp, "normalised_body", [
            row("stone_wall_1x1", 8, 0.0, 2),
            row("stone_wall_1x1", 8, 1.0, 2),
            row("stone_floor_2x2", 10, 1.0, 2),
            row("stone_floor_2x2", 12, 1.0, 2),
            row("piece_chest_wood", 10, 1.5, 2),
        ])
        objs = read_objects(path)
        self.assertEqual(min(o.pos[1] for o in objs), 0.0)
        self.assertEqual(old_rule_dy(objs), 0.0)

        datum = bg.floor_datum(objs, self.g)
        self.assertTrue(datum.placeable)
        self.assertNotEqual(round(datum.base_y, 6), 0.0,
                            "the floor datum must not collapse to a no-op shift")
        self.assertAlmostEqual(datum.base_y, 1.5, places=6)

    def test_terraces_report_the_residual_of_a_slope_captured_body(self):
        # Two floor levels 1 m apart cannot both be flush with a flat pad. The
        # lower one is, and the residual is reported rather than hidden.
        path = blueprint(self.tmp, "two_terraces", [
            row("stone_floor_2x2", 0, 0.5, 0),
            row("stone_floor_2x2", 2, 0.5, 0),
            row("stone_floor_2x2", 4, 1.5, 0),
            row("stone_floor_2x2", 6, 1.5, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertAlmostEqual(datum.base_y, 1.0, places=6)
        self.assertEqual(datum.terraces_m(), [0.0, 1.0])

    def test_a_buried_station_beats_the_floor_above_it(self):
        # A raised deck over ground-standing stations: the camp case. A buried
        # workbench cannot be crafted at, so the station plane wins.
        path = blueprint(self.tmp, "raised_deck", [
            row("wood_floor_1x1", 0, 1.5, 0),
            row("wood_floor_1x1", 1, 1.5, 0),
            row("piece_workbench", 4, 0.0, 0),
            row("forge", 6, 0.0, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertEqual(datum.method, "lowest_station_below_floor")
        self.assertAlmostEqual(datum.base_y, 0.0, places=6)
        self.assertIn("station_below_floor", [v["code"] for v in datum.violations])

    def test_an_outdoor_prop_below_the_floor_does_not_move_the_datum(self):
        # A barrel that stood on lower ground outside the house is reported, not
        # obeyed: burying the floor to suit it would make the house unenterable.
        path = blueprint(self.tmp, "outdoor_prop", [
            row("stone_floor_2x2", 0, 0.5, 0),
            row("stone_floor_2x2", 2, 0.5, 0),
            row("piece_chest_wood", 8, -0.8, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertAlmostEqual(datum.base_y, 1.0, places=6)
        self.assertIn("props_below_floor", [v["code"] for v in datum.violations])
        self.assertAlmostEqual(datum.props_below_floor_m, 1.8, places=3)

    def test_a_floorless_body_uses_its_lowest_build_piece_not_its_decor(self):
        # World decor picked up by blueprint capture is modelled to sit IN the
        # ground: a rock whose solid reaches metres below its pivot must not
        # define the datum, or the structure is lifted by that much.
        path = blueprint(self.tmp, "floorless", [
            row("stone_arch", 0, 1.0, 0),
            row("wood_beam", 2, 1.0, 0),
            row("Rock_4", 4, -2.6, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertEqual(datum.method, "lowest_build_solid_no_floor")
        self.assertIn("no_floor_used_lowest_solid", [v["code"] for v in datum.violations])
        self.assertLess(datum.bottom_solid, -2.0, "the rock really is that deep")
        self.assertGreater(datum.base_y, datum.bottom_solid + 2.0)

    def test_a_sub_cellar_slab_with_nothing_on_it_is_skipped(self):
        path = blueprint(self.tmp, "sub_cellar", [
            row("wood_floor_1x1", 0, -7.0969, 0),
        ] + [
            row("stone_floor_2x2", 2 * i, -6.5, 0) for i in range(30)
        ] + [
            row("piece_chest_wood", 4, -6.0, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertEqual(datum.method, "lowest_walkable_surface_above_cellar")
        self.assertAlmostEqual(datum.base_y, -6.0, places=6)
        self.assertIn("suspect_below_grade_floor", [v["code"] for v in datum.violations])

    def test_an_unmeasured_floor_prefab_refuses_rather_than_guesses(self):
        path = blueprint(self.tmp, "unknown_floor", [
            row("modded_marble_floor_3x3", 0, 1.0, 0),
            row("stone_wall_2x1", 0, 2.0, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertFalse(datum.placeable)
        self.assertIsNone(datum.base_y)
        self.assertEqual([v["code"] for v in datum.violations], ["unmeasured_floor_prefab"])
        with self.assertRaises(ValueError):
            datum.place_y(70.91)

    def test_a_body_with_no_measurable_solid_refuses(self):
        path = blueprint(self.tmp, "all_unknown", [
            row("some_mod_prefab_nobody_ships", 0, 1.0, 0),
        ])
        datum = bg.floor_datum(read_objects(path), self.g)
        self.assertFalse(datum.placeable)
        self.assertEqual([v["code"] for v in datum.violations], ["no_measurable_solid"])


class EmitterIntegration(unittest.TestCase):
    """`to_rcon_plan.py --align floor-center` has to actually apply the datum."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _plan(self, path: Path, *extra: str):
        out = self.tmp / (path.stem + "".join(extra).replace("-", "") + ".txt")
        proc = subprocess.run(
            [sys.executable, str(BLUEPRINTS / "to_rcon_plan.py"), str(path),
             "--at", "0", "70.91", "0", "--allow-missing-prefabs",
             "--out", str(out), *extra],
            capture_output=True, text=True,
        )
        return proc, out

    def test_floor_center_puts_the_walkable_surface_on_the_requested_y(self):
        path = blueprint(self.tmp, "emit_floor", [
            row("stone_wall_1x1", 0, 0.0, 0),
            row("stone_floor_2x2", 0, 1.0, 0),
            row("stone_floor_2x2", 2, 1.0, 0),
        ])
        proc, out = self._plan(path, "--align", "floor-center")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ys = sorted({float(line.split()[3]) for line in out.read_text().splitlines()})
        # floor pivots land 0.5 below the pad so their tops are ON it
        self.assertIn(70.41, ys)
        self.assertIn(69.41, ys)

        # the old rule, for contrast: this body is normalised, so it shifts
        # nothing and the floor tops end up 1.5 m above the requested Y
        proc, out = self._plan(path, "--align", "ground-center")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ys_old = sorted({float(line.split()[3]) for line in out.read_text().splitlines()})
        self.assertEqual(ys_old, [70.91, 71.91])

    def test_the_emitter_refuses_an_unplaceable_body(self):
        path = blueprint(self.tmp, "emit_refuse", [
            row("modded_marble_floor_3x3", 0, 1.0, 0),
        ])
        proc, _ = self._plan(path, "--align", "floor-center")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("floor plane cannot be established", proc.stderr)

    def test_a_declared_base_y_overrides_the_measured_one(self):
        path = blueprint(self.tmp, "emit_override", [
            row("stone_floor_2x2", 0, 1.0, 0),
            row("stone_floor_2x2", 2, 1.0, 0),
        ])
        proc, out = self._plan(path, "--align", "floor-center", "--base-y", "0.0")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("datum override", proc.stderr)
        ys = sorted({float(line.split()[3]) for line in out.read_text().splitlines()})
        self.assertEqual(ys, [71.91])


if __name__ == "__main__":
    unittest.main()
