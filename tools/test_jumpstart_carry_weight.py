#!/usr/bin/env python3
"""Tests for the jumpstart CARRY WEIGHT contract: a kit the character can carry.

The defect these pin down, MEASURED on Ulfsland on 2026-09-15 before the fix:

  * `pre-bonemass/charactertemplate.yml` granted 30 prefabs into 32 of 32
    inventory slots weighing **426.8 kg**. A Valheim character carries 300 kg
    (`Player..ctor` sets `m_maxCarryWeight = 300f`; `IsEncumbered()` is
    `Inventory.GetTotalWeight() > GetMaxCarryWeight()`), so the operator's new
    character spawned OVERBURDENED and could not walk away from the base.
    `derive.py` modelled slots and not weight, so it happily emitted it.
    `pre-eikthyr` (410.4 kg) and `pre-elder` (440.5 kg) were the same.

  * Nothing granted by the template is EQUIPPED -- the string `EquipItem` does
    not occur anywhere in ServerCharacters.dll -- so the one vanilla item that
    raises carry capacity, `BeltStrength` (+150, MEASURED out of the game's
    bundles), contributes ZERO on spawn and cannot be used to pay for a heavy
    kit. And `95Shade-CarryWeightSkill`'s +3/level is computed client-side only
    (its DLL is absent from the server plugin tree), so it cannot back a
    server-side guarantee either. The only lever that works is a lighter kit.

So: every preset's rendered template must weigh no more than the world's carry
budget, and a preset that cannot be trimmed to fit must be REFUSED by the build
rather than reaching a player. The operator found this by being unable to move;
all of it was computable before anyone logged in.

Everything here is offline: the real presets, the real world.yaml and the real
measured weight table, rendered into a temporary directory. No server, no live
file.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent
DERIVE_PATH = TOOLS / "jumpstart" / "worlds" / "derive.py"
REAL_WORLD = TOOLS / "jumpstart" / "worlds" / "Ulfsland" / "world.yaml"
WEIGHT_TABLE = TOOLS / "jumpstart" / "data" / "item_weights.json"
PRESET_DIR = TOOLS / "jumpstart" / "presets"


def load_derive():
    spec = importlib.util.spec_from_file_location("jumpstart_derive_weight", DERIVE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["jumpstart_derive_weight"] = module
    spec.loader.exec_module(module)
    return module


derive = load_derive()
WEIGHTS = json.loads(WEIGHT_TABLE.read_text("utf-8"))["weights"]


def template_items(text: str) -> dict[str, int]:
    """The `items:` map as ServerCharacters' YamlDotNet would see it.

    Parsed out of the rendered file rather than read off a Partition, because
    the file is what reaches the player and the comments around it are not.
    """
    doc = yaml.safe_load(text)
    return {str(k): int(v) for k, v in (doc.get("items") or {}).items()}


def total_kg(items: dict[str, int]) -> float:
    # ItemDrop.ItemData.GetWeight = m_shared.m_weight * stack, plus a quality
    # term that never applies here: ServerCharacters' AddItem passes the literal
    # quality 1, and GetWeight only scales above quality 1.
    return sum(WEIGHTS[prefab] * count for prefab, count in items.items())


class MeasuredCapacity(unittest.TestCase):
    """The numbers the budget rests on are in world.yaml, not in someone's head."""

    def setUp(self):
        self.world = derive.load_world("Ulfsland")

    def test_capacity_and_budget_are_declared_and_leave_real_headroom(self):
        self.assertEqual(self.world.carry_capacity_kg, 300.0)
        # A kit at 100% of capacity is as unusable as one over it: IsEncumbered
        # is a strict greater-than, so 300.0 kg is technically not encumbered and
        # still cannot accept one berry. The budget has to be a real fraction.
        self.assertLess(self.world.carry_budget_fraction, 1.0)
        self.assertGreaterEqual(self.world.carry_capacity_kg - self.world.carry_budget_kg, 60.0)

    def test_every_preset_prefab_has_a_measured_weight(self):
        missing = {}
        for name in derive.js.all_preset_names():
            preset = derive.js.load_preset(name)
            merged, _ordered, _dupes = derive.item_map(preset)
            absent = sorted(p for p in merged if p not in WEIGHTS)
            if absent:
                missing[name] = absent
        self.assertEqual(missing, {}, f"unpriced prefabs: {missing}")


class EveryPresetFitsTheCharacter(unittest.TestCase):
    """The regression. Pre-fix this failed on pre-eikthyr, pre-elder, pre-bonemass."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.world_root = root / "TestWorld"
        self.world_root.mkdir(parents=True)
        (self.world_root / "world.yaml").write_text(
            REAL_WORLD.read_text("utf-8").replace("world: Ulfsland", "world: TestWorld", 1),
            "utf-8",
        )
        self._here = derive.HERE
        derive.HERE = root
        self.addCleanup(setattr, derive, "HERE", self._here)
        self.world = derive.load_world("TestWorld")

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = derive.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_build_accepts_every_shipped_preset(self):
        code, _out, err = self.cli("build", "TestWorld")
        self.assertEqual(code, 0, err)
        self.assertNotIn("KIT", err)

    def test_rendered_template_is_under_capacity_for_every_preset(self):
        code, _out, err = self.cli("build", "TestWorld")
        self.assertEqual(code, 0, err)
        budget = self.world.carry_budget_kg
        capacity = self.world.carry_capacity_kg
        over = {}
        for name in derive.js.all_preset_names():
            path = self.world_root / name / "charactertemplate.yml"
            items = template_items(path.read_text("utf-8"))
            self.assertTrue(items, f"{name}: rendered an empty kit")
            load = total_kg(items)
            if load > budget:
                over[name] = round(load, 1)
            # The hard game rule, independent of the budget: strictly greater
            # than capacity is what IsEncumbered() reports.
            self.assertLessEqual(
                load, capacity, f"{name} would spawn overburdened at {load:.1f} kg"
            )
        self.assertEqual(over, {}, f"over the {budget:g} kg budget: {over}")

    def test_template_states_the_load_it_grants(self):
        code, _out, err = self.cli("build", "TestWorld", "--preset", "pre-bonemass")
        self.assertEqual(code, 0, err)
        text = (self.world_root / "pre-bonemass" / "charactertemplate.yml").read_text("utf-8")
        load = total_kg(template_items(text))
        self.assertIn(f"{load:g} of {self.world.carry_capacity_kg:g} kg carried", text)
        self.assertIn("CARRY LOAD:", text)


class OverweightPresetIsRefused(unittest.TestCase):
    """The guard. A kit that cannot be trimmed to fit must not render at all."""

    PRESET = "test-overweight"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.world_root = root / "TestWorld"
        (self.world_root / self.PRESET).mkdir(parents=True)
        (self.world_root / "world.yaml").write_text(
            REAL_WORLD.read_text("utf-8").replace("world: Ulfsland", "world: TestWorld", 1),
            "utf-8",
        )
        self.preset_dir = root / "presets"
        self.preset_dir.mkdir()
        self._here, self._presets = derive.HERE, derive.js.PRESET_DIR
        derive.HERE = root
        derive.js.PRESET_DIR = self.preset_dir
        self.addCleanup(setattr, derive, "HERE", self._here)
        self.addCleanup(setattr, derive.js, "PRESET_DIR", self._presets)

    def write_preset(self, kit: list[dict], materials: list[dict] | None = None) -> None:
        (self.preset_dir / f"{self.PRESET}.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": self.PRESET,
                    "tier": 3,
                    "biome": "Swamp",
                    "boss": "Bonemass",
                    "summary": "synthetic",
                    "global_keys": [],
                    "world_modifiers": {},
                    "kit": kit,
                    "materials": materials or [],
                    "chain_items": [],
                    "skills": {},
                    "stations": [],
                },
                sort_keys=False,
            ),
            "utf-8",
        )

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = derive.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_heavy_kit_is_refused_and_no_template_is_written(self):
        # Iron is 12 kg a bar and stacks 30 to a slot (both MEASURED), so this kit
        # is 720 kg of metal in two slots plus a 15 kg chestpiece: it fits the grid
        # with room to spare, which is exactly the shape of the bug. It is in `kit`,
        # not `materials`, so it is undroppable and the build has to refuse.
        self.write_preset(
            [
                {"prefab": "ArmorIronChest", "quality": 1, "count": 1},
                {"prefab": "Iron", "quality": 1, "count": 30},
                {"prefab": "Bronze", "quality": 1, "count": 30},
            ]
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("KIT", err)
        self.assertIn("over the 210 kg budget", err)
        self.assertIn("Iron x30 = 360 kg", err)
        self.assertIn(f"tools/jumpstart/presets/{self.PRESET}.yaml", err)
        self.assertIn("HELD", out)
        self.assertFalse((self.world_root / self.PRESET / "charactertemplate.yml").exists())
        # The chest manifest still renders: it is what the operator needs in order
        # to see where the weight went.
        self.assertTrue(
            (self.world_root / self.PRESET / "settings" / "chest-manifest.yaml").exists()
        )

    def test_the_same_kit_trimmed_to_fit_is_accepted(self):
        self.write_preset(
            [
                {"prefab": "ArmorIronChest", "quality": 1, "count": 1},
                {"prefab": "Iron", "quality": 1, "count": 5},
                {"prefab": "Bronze", "quality": 1, "count": 5},
            ]
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 0, err)
        template = (self.world_root / self.PRESET / "charactertemplate.yml").read_text("utf-8")
        self.assertEqual(total_kg(template_items(template)), 15.0 + 60.0 + 60.0)

    def test_an_unpriced_prefab_is_refused_rather_than_assumed_weightless(self):
        self.write_preset([{"prefab": "NotAnItemAtAll", "quality": 1, "count": 1}])
        code, _out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1)
        self.assertIn("no measured weight", err)
        self.assertIn("NotAnItemAtAll", err)
        self.assertFalse((self.world_root / self.PRESET / "charactertemplate.yml").exists())

    def test_materials_are_trimmed_to_the_budget_instead_of_refusing(self):
        """Materials are droppable -- the base has chests -- so they lose, not the kit."""
        self.write_preset(
            [{"prefab": "ArmorIronChest", "quality": 1, "count": 1}],
            materials=[
                {"prefab": "Iron", "count": 30},    # 12 kg each = 360 kg, cannot come
                {"prefab": "Stone", "count": 50},   # 2 kg each  = 100 kg
                {"prefab": "Wood", "count": 25},    # 2 kg each  =  50 kg
            ],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 0, err)
        items = template_items(
            (self.world_root / self.PRESET / "charactertemplate.yml").read_text("utf-8")
        )
        self.assertIn("ArmorIronChest", items)
        self.assertLessEqual(total_kg(items), 210.0)
        manifest = yaml.safe_load(
            (self.world_root / self.PRESET / "settings" / "chest-manifest.yaml").read_text("utf-8")
        )
        # 360 kg of Iron cannot come along; the lighter Stone and Wood can.
        self.assertIn("Iron", manifest["dropped_for_weight"])
        self.assertEqual(sorted(items), ["ArmorIronChest", "Stone", "Wood"])


if __name__ == "__main__":
    unittest.main()
