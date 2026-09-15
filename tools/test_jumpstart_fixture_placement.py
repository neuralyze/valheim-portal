#!/usr/bin/env python3
"""No loose fixture may stand inside the building -- for every preset, every site.

THE DEFECT THIS EXISTS FOR
--------------------------
Reported from live play on Ulfsland on 2026-09-15, by an operator walking up to
the finished `pre-bonemass/iron-era-workshop`: "the position isnt correct, there
are portals in the middle of walls".

MEASURED, box-testing what `terraform/stock.py` placed against all 1,906 solids
of the blueprint body placed before it: 12 of its 18 fixtures were INSIDE the
structure. `portal_wood` at pad-offset (-6.00, 0.00) overlapped a
`stone_wall_4x2` by 1.00 x 2.00 x 1.18 m. `piece_maypole` pierced 22 solids,
`smelter` 20, `piece_workbench` 11. The minimum fixture-to-wall gap across the
set was 0.000 m.

Cause: `stock.py` placed from hardcoded offsets relative to the PAD CENTRE,
solved when the body was anchored on its own corner and the middle of the pad
was empty ground. `to_rcon_plan.py` was then fixed to centre the body on its
measured footprint, the building moved onto the middle of the pad, and the
offsets did not follow.

WHY IT IS A TEST AND NOT A REPORT
---------------------------------
The operator found this by walking into it. Nothing in the pipeline was asking
the question, so the answer was never wrong -- it was absent. This test asks it
for every placement of every preset, offline, from the same transform the
emitter uses, and FAILS rather than warning: a portal inside a wall is not a
degraded placement, it is a broken one.

The failure message carries the measured overlap, because "fixture collides"
does not tell an operator which fixture, in what, or by how much.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent
JUMPSTART = TOOLS / "jumpstart"
BLUEPRINTS = JUMPSTART / "blueprints"
TERRAFORM = JUMPSTART / "terraform"
sys.path.insert(0, str(BLUEPRINTS))
sys.path.insert(0, str(TERRAFORM))

import base_geometry as bg  # noqa: E402
import fixtures  # noqa: E402
import stock  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402

WORLDS = JUMPSTART / "worlds"


def placements() -> list[tuple[str, str, dict]]:
    """(world, preset, placement) for everything that has a solved site."""
    out = []
    for world_dir in sorted(WORLDS.iterdir()):
        if not world_dir.is_dir():
            continue
        for preset_dir in sorted(world_dir.iterdir()):
            doc = preset_dir / "placements.yaml"
            if not doc.is_file():
                continue
            loaded = yaml.safe_load(doc.read_text())
            for place in loaded.get("placements") or []:
                if place.get("solved"):
                    out.append((world_dir.name, preset_dir.name, place))
    return out


class FixtureGeometry(unittest.TestCase):
    """The primitives the guard is built on, asserted so a wrong box test cannot
    quietly pass everything."""

    @classmethod
    def setUpClass(cls):
        cls.g = bg.geometry()

    def test_overlapping_boxes_report_their_penetration(self):
        a = (0.0, 2.0, 0.0, 1.0, 0.0, 2.0, "a")
        b = (1.5, 3.0, 0.5, 2.0, 1.0, 4.0, "b")
        ox, oy, oz = fixtures.overlap(a, b)
        self.assertAlmostEqual(ox, 0.5)
        self.assertAlmostEqual(oy, 0.5)
        self.assertAlmostEqual(oz, 1.0)
        self.assertEqual(fixtures.gap(a, b), 0.0)

    def test_touching_boxes_do_not_count_as_intersecting(self):
        a = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0, "a")
        b = (1.0, 2.0, 0.0, 1.0, 0.0, 1.0, "b")
        self.assertLessEqual(fixtures.overlap(a, b)[0], 0.0)
        self.assertEqual(fixtures.gap(a, b), 0.0)

    def test_separated_boxes_report_their_distance(self):
        a = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0, "a")
        b = (4.0, 5.0, 0.0, 1.0, 0.0, 1.0, "b")
        self.assertAlmostEqual(fixtures.gap(a, b), 3.0)

    def test_a_fixture_box_stands_on_the_surface_it_is_given(self):
        # Every ground-resting prefab's solid starts at its own pivot, which is
        # why a prop spawned at the pad height rests on the pad.
        box = fixtures.fixture_box("piece_chest_wood", 10.0, 70.91, -5.0, 0.0, self.g)
        self.assertAlmostEqual(box[2], 70.91, places=2)
        self.assertGreater(box[3], box[2])

    def test_the_fixture_role_set_catches_kit_variants(self):
        for prefab in ("piece_chest_wood", "piece_chest_blackmetal", "portal_wood",
                       "charcoal_kiln", "piece_workbench", "hearth", "bed",
                       "piece_maypole", "fire_pit"):
            with self.subTest(prefab=prefab):
                self.assertTrue(fixtures.is_fixture(prefab))
        for prefab in ("stone_wall_2x1", "wood_floor", "stone_floor_2x2", "wood_roof"):
            with self.subTest(prefab=prefab):
                self.assertFalse(fixtures.is_fixture(prefab))


class FixturesClearTheStructure(unittest.TestCase):
    """The guard itself, over every solved placement in the repository."""

    @classmethod
    def setUpClass(cls):
        cls.cases = placements()
        cls.g = bg.geometry()

    def test_there_are_placements_to_check(self):
        # A guard that silently checks nothing is the failure mode this whole
        # module is a reaction to.
        self.assertGreaterEqual(len(self.cases), 8)

    def test_no_planned_fixture_stands_inside_its_building(self):
        checked = 0
        for world, preset, place in self.cases:
            with self.subTest(site=f"{preset}/{place['id']}"):
                report = json.loads(subprocess.run(
                    [sys.executable, str(TERRAFORM / "stock.py"),
                     "--world", world, "--preset", preset, "--id", place["id"],
                     "--plan"],
                    capture_output=True, text=True, check=True).stdout)
                audit = report.get("audit")
                if audit is None:
                    self.assertIsNone(report["plan"]["blueprint"],
                                      "a placement with a blueprint must be audited")
                    continue
                checked += 1
                self.assertEqual(
                    audit["failures"], [],
                    f"{preset}/{place['id']}: " + "; ".join(audit["failures"]))
                # A PROP or a CHEST with nowhere to stand is a site that cannot
                # be stocked, so it fails. An EXTENSION with nowhere to stand is
                # a station the blueprint's own builder walled in: the preset's
                # level target is then unmet, which is a fact about that body
                # and is reported, not a placement bug. Either way nothing is
                # ever put inside the structure, which is what `failures` says.
                hard = [u for u in report["unplaceable"] if u["role"] != "extension"]
                self.assertEqual(hard, [],
                                 f"{preset}/{place['id']}: nowhere legal to stand")
                self.assertGreater(audit["min_wall_clearance_m"], 0.0,
                                   f"{preset}/{place['id']}: a fixture touches a wall")
        self.assertGreater(checked, 0, "no placement was actually audited")

    def test_a_station_the_blueprint_carries_is_not_placed_again(self):
        # The duplication half of the defect: nine of the ten station spawns on
        # iron-era-workshop were second copies standing in the walls of the
        # first, because the preset names them and the body already had them.
        report = json.loads(subprocess.run(
            [sys.executable, str(TERRAFORM / "stock.py"),
             "--world", "Ulfsland", "--preset", "pre-bonemass",
             "--id", "iron-era-workshop", "--plan"],
            capture_output=True, text=True, check=True).stdout)
        plan = report["plan"]
        carried = plan["carried_by_blueprint"]
        self.assertEqual(carried.get("portal_wood"), 1)
        self.assertEqual(carried.get("piece_chest_wood"), 17)
        for row in plan["stations"]:
            with self.subTest(station=row["prefab"]):
                if row["carried_by_blueprint"]:
                    self.assertIsNone(row["place"],
                                      f"{row['prefab']} is in the blueprint already")
                    self.assertEqual(row["owner"], "blueprint")
        # and the props it carries are declared, not silently dropped
        skipped = {s["prefab"] for s in plan["skipped"]}
        self.assertIn("portal_wood", skipped)
        self.assertIn("piece_cartographytable", skipped)

    def test_the_station_levels_are_still_topped_up(self):
        # Ownership must not cost the preset its station LEVELS: the body has a
        # workbench with two extensions, the preset requires level 5, so two
        # more extensions are placed beside the blueprint's own workbench.
        report = json.loads(subprocess.run(
            [sys.executable, str(TERRAFORM / "stock.py"),
             "--world", "Ulfsland", "--preset", "pre-bonemass",
             "--id", "iron-era-workshop", "--plan"],
            capture_output=True, text=True, check=True).stdout)
        bench = next(r for r in report["plan"]["stations"]
                     if r["prefab"] == "piece_workbench")
        self.assertEqual(bench["required_level"], 5)
        self.assertEqual(bench["extensions_carried"], 2)
        self.assertEqual(bench["extensions_needed"], 2)
        self.assertEqual(len(bench["extensions"]), 2)
        for ext in bench["extensions"]:
            self.assertIsNotNone(ext["place"], ext["prefab"])
            self.assertLessEqual(ext["distance_m"], 3.6)
        # placed at the blueprint workbench's own floor, not at the pad
        pad_y = 70.91
        for ext in bench["extensions"]:
            self.assertGreater(ext["place"]["y"], pad_y)


class TheDefectItself(unittest.TestCase):
    """The offsets that were live in the world, asserted to be caught.

    This is the regression test proper: it re-creates what `stock.py` placed on
    `iron-era-workshop` and requires the audit to reject it. Before the fix
    `stock.py` had no audit at all, so this could not have been asked.
    """

    BODY = Path("/media/big4/projects/game/valheim/old/old/old/old_Storgard/"
                "config_merged/BepInEx/PlanBuild/blueprints/"
                "BjOrN_blueprint001.blueprint")
    # what was in the world, MEASURED with `findObjects -prefab ... -near`:
    # every one of these sat at exactly the pad height with a round pad offset.
    WAS_PLACED = [
        ("piece_workbench", 10.0, 10.0), ("forge", 5.0, 10.0),
        ("piece_cauldron", 0.0, 10.0), ("smelter", -5.0, 10.0),
        ("charcoal_kiln", -10.0, 10.0), ("piece_chest_wood", -15.0, 10.0),
        ("portal_wood", -20.0, 10.0), ("hearth", -25.0, 10.0),
        ("piece_stonecutter", -30.0, 10.0), ("portal_wood", -6.0, 0.0),
        ("fire_pit", 0.0, -4.0), ("bed", 4.0, -4.0), ("bed", 6.0, -4.0),
        ("piece_cartographytable", -4.0, 4.0), ("piece_maypole", 0.0, -8.0),
        ("piece_chest", -1.6, -12.0), ("piece_chest", 0.0, -12.0),
        ("piece_chest", 1.6, -12.0),
    ]
    CX, CZ, PAD = -4673.5, -310.5, 70.91

    @classmethod
    def setUpClass(cls):
        if not cls.BODY.is_file():
            raise unittest.SkipTest(f"{cls.BODY} not present")
        cls.objs = [o for o in read_objects(cls.BODY) if o.prefab != "piece_Sundial"]

    def _audit(self, base_y):
        body = fixtures.place_body(self.objs, at=(self.CX, self.PAD, self.CZ),
                                   yaw_deg=180.0, base_y=base_y)
        return body, fixtures.audit(
            [(p, self.CX + dx, self.PAD, self.CZ + dz, 180.0)
             for p, dx, dz in self.WAS_PLACED], body)

    def test_the_live_offsets_are_rejected_with_the_measured_overlap(self):
        _body, audit = self._audit(1.5)   # the datum the world was placed with
        self.assertEqual(audit["min_clearance_m"], 0.0)
        self.assertEqual(len(audit["failures"]), 12,
                         "12 of the 18 fixtures stood inside the building")
        portal = [f for f in audit["failures"]
                  if f.startswith("portal_wood") and "-4679.50" in f]
        self.assertEqual(len(portal), 1, audit["failures"])
        self.assertIn("overlaps stone_wall_4x2 by 1.00 x 2.00 x 1.18 m", portal[0])

    def test_the_body_transform_matches_the_emitter(self):
        # The guard is only as good as the building it tests against. These are
        # positions read back from the LIVE server with `findObjects`, and the
        # reconstruction has to land on them.
        body = fixtures.place_body(self.objs, at=(self.CX, self.PAD, self.CZ),
                                   yaw_deg=180.0, base_y=1.5)
        ox, oy, oz = body.origin
        live = {
            "portal_wood": (-4671.92, 82.34, -310.68),
            "forge": (-4689.45, 75.52, -323.47),
            "hearth": (-4663.08, 75.24, -317.10),
            "piece_stonecutter": (-4655.50, 71.53, -327.46),
            "piece_cartographytable": (-4665.45, 78.32, -310.38),
        }
        for prefab, (lx, ly, lz) in live.items():
            with self.subTest(prefab=prefab):
                best = min(
                    ((-o.pos[0] + ox - lx) ** 2 + (o.pos[1] + oy - ly) ** 2
                     + (-o.pos[2] + oz - lz) ** 2) ** 0.5
                    for o in self.objs if o.prefab == prefab)
                self.assertLess(best, 0.02, f"{prefab} reconstructed {best:.3f} m off")


if __name__ == "__main__":
    unittest.main()
