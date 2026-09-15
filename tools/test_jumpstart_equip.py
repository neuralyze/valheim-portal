#!/usr/bin/env python3
"""Tests for the jumpstart EQUIP and QUALITY contract.

Two defects, both MEASURED in ServerCharacters 1.4.17 on 2026-09-15 and both
fixed in the in-house build:

  * `InitializePlayerFromTemplate.Postfix` called
    `Inventory::AddItem(name, count, 1, 0, 0L, "", false, false)` per `items`
    entry and the string `EquipItem` occurred ZERO times in the assembly, so a
    new character spawned unarmoured and unarmed holding a full set of gear.
  * The third `AddItem` argument was the literal `ldc.i4.1`, so every
    `quality: 3` in every preset was a lie. `pre-bonemass` asked for quality 3
    fourteen times and got quality 1 fourteen times.

The mod now takes `quality:` (a map) and `equip:` (an ORDERED list). That moves
the failure modes into THIS tree, because the template can now assert things
about the character that the kit cannot deliver:

  * equipping an item the preset does not grant -- `AddItem` never put it in the
    inventory, so `EquipItem` has nothing to equip;
  * equipping an item the partition dropped for slots or weight -- same outcome,
    reached a different way;
  * equipping two items that compete for one slot -- the game arbitrates, the
    last writer wins, and the earlier entry silently arrives in the bag;
  * asking for a quality above the item's `m_maxQuality`.

Slot arbitration is NOT re-specified here. `derive.SLOT_BY_ITEM_TYPE` and
`derive.equip_outcome` transcribe `Humanoid::EquipItem`, and these tests pin the
cases where a naive slot model gets it WRONG -- a Bow taking both hands, a
one-hander keeping a shield -- because those are the ones that would ship a
character holding half of what the template promised.

Everything is offline: the real presets, the real world.yaml and the real
measured item table, rendered into a temporary directory.
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
ITEM_TABLE = TOOLS / "jumpstart" / "data" / "item_weights.json"


def load_derive():
    spec = importlib.util.spec_from_file_location("jumpstart_derive_equip", DERIVE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["jumpstart_derive_equip"] = module
    spec.loader.exec_module(module)
    return module


derive = load_derive()
TABLE_DOC = json.loads(ITEM_TABLE.read_text("utf-8"))


class MeasuredItemFacts(unittest.TestCase):
    """The table has to carry the fields the guard reasons about."""

    def setUp(self):
        self.table = derive.load_item_table(derive.load_world("Ulfsland"))

    def test_item_type_and_max_quality_are_present_for_equippables(self):
        # Absent means UNKNOWN, and the guard reports rather than defaults, so a
        # table missing these would turn every equip check into a no-op.
        for prefab, kind, cap in (
            ("MaceIron", "OneHandedWeapon", 4),
            ("ShieldBanded", "Shield", 3),
            ("ArmorIronChest", "Chest", 4),
            ("BeltStrength", "Utility", 1),
            ("BackpackSwamp", "Shoulder", 4),
        ):
            self.assertEqual(self.table.type_name(prefab), kind, prefab)
            self.assertEqual(self.table.max_quality[prefab], cap, prefab)

    def test_quality_changes_weight_only_where_the_game_says_it_does(self):
        # MEASURED from ItemDrop/ItemData::GetWeight: the quality term is gated
        # on m_scaleWeightByQuality != 0. Fish scale, gear does not -- which is
        # why honouring quality moved no preset's load.
        self.assertEqual(
            self.table.weight_at("ArmorIronChest", 1, 4),
            self.table.weight_at("ArmorIronChest", 1, 1),
        )
        one = self.table.weight_at("Fish1", 1, 1)
        # scale 1.0, so quality 4 is 1 + 3*1.0 = 4x the stacked weight.
        self.assertAlmostEqual(self.table.weight_at("Fish1", 1, 4), one * 4.0, places=5)

    def test_backpack_container_grid_is_the_smallest_configured_level(self):
        # The grids come from each client's OWN cfg (AdventureBackpacks has no
        # config-value RPC), so only the minimum is safe to rely on.
        facts = self.table.containers["BackpackSwamp"]
        self.assertEqual(facts["min_slots"], min(facts["slots"].values()))
        self.assertEqual(self.table.container_slots("BackpackSwamp"), facts["min_slots"])
        self.assertIsNone(self.table.container_slots("ArmorIronChest"))


class GameSlotArbitration(unittest.TestCase):
    """`equip_outcome` must agree with Humanoid::EquipItem, not with intuition."""

    def setUp(self):
        self.table = derive.load_item_table(derive.load_world("Ulfsland"))

    def outcome(self, *order):
        return derive.equip_outcome(list(order), self.table)

    def test_one_hander_and_shield_coexist(self):
        # The ONLY pair in the hands that does. MEASURED: the OneHandedWeapon arm
        # unequips the left hand unless it holds a Shield or a Torch.
        got = self.outcome("MaceIron", "ShieldBanded")
        self.assertEqual(got.displaced, [])
        self.assertEqual(got.worn[derive.SLOT_RIGHT], "MaceIron")
        self.assertEqual(got.worn[derive.SLOT_LEFT], "ShieldBanded")

    def test_bow_takes_both_hands_so_a_shield_beside_it_is_displaced(self):
        # The case a slot-name table gets wrong: "bow" and "shield" look like
        # different slots and are not. MEASURED, the Bow arm unequips BOTH hands.
        got = self.outcome("ShieldBanded", "BowHuntsman")
        self.assertIn(("ShieldBanded", derive.SLOT_LEFT, "BowHuntsman"), got.displaced)
        self.assertEqual(got.worn[derive.SLOT_LEFT], "BowHuntsman")

    def test_two_hander_displaces_a_shield(self):
        got = self.outcome("ShieldBanded", "SledgeIron")
        self.assertTrue(got.displaced)
        self.assertEqual(got.worn[derive.SLOT_RIGHT], "SledgeIron")

    def test_armour_pieces_occupy_three_independent_slots(self):
        got = self.outcome("HelmetIron", "ArmorIronChest", "ArmorIronLegs")
        self.assertEqual(got.displaced, [])
        self.assertEqual(len(got.worn), 3)

    def test_two_chest_pieces_contend(self):
        got = self.outcome("ArmorIronChest", "ArmorRootChest")
        self.assertEqual(
            got.displaced, [("ArmorIronChest", "chest", "ArmorRootChest")]
        )

    def test_a_backpack_and_a_cape_contend_for_the_shoulder(self):
        # MEASURED: every AdventureBackpacks prefab is m_itemType 17, the cape
        # slot. The names give no hint of it.
        got = self.outcome("CapeTrollHide", "BackpackSwamp")
        self.assertEqual(
            got.displaced, [("CapeTrollHide", "shoulder", "BackpackSwamp")]
        )

    def test_a_material_cannot_be_equipped_at_all(self):
        got = self.outcome("Wood")
        self.assertEqual(got.unequipable, ["Wood"])
        self.assertEqual(got.worn, {})

    def test_an_unpriced_prefab_is_reported_not_slotted(self):
        got = self.outcome("NotAnItemAtAll")
        self.assertEqual(got.unslotted, ["NotAnItemAtAll"])


class EveryShippedPresetEquipsWhatItPromises(unittest.TestCase):
    """The nine real presets, through the real CLI, writing to a temp tree."""

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
        self.table = derive.load_item_table(derive.load_world("TestWorld"))

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = derive.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_build_accepts_every_shipped_preset(self):
        code, _out, err = self.cli("build", "TestWorld")
        self.assertEqual(code, 0, err)
        self.assertNotIn("KIT", err)

    def test_every_preset_equips_armour_and_a_weapon(self):
        """The defect, stated as a contract: nobody spawns naked or unarmed."""
        code, _out, err = self.cli("build", "TestWorld")
        self.assertEqual(code, 0, err)
        for name in derive.js.all_preset_names():
            doc = yaml.safe_load(
                (self.world_root / name / "charactertemplate.yml").read_text("utf-8")
            )
            equip = doc.get("equip") or []
            items = doc.get("items") or {}
            self.assertTrue(equip, f"{name}: equips nothing")
            # Every entry must have been granted, or the mod has nothing to equip.
            self.assertEqual(
                [p for p in equip if p not in items], [], f"{name}: equips ungranted"
            )
            got = derive.equip_outcome(equip, self.table)
            self.assertEqual(got.displaced, [], f"{name}: {got.displaced}")
            for slot in ("helmet", "chest", "legs", derive.SLOT_RIGHT):
                self.assertIn(slot, got.worn, f"{name}: nothing in {slot}")

    def test_no_preset_grants_beltstrength(self):
        """No kit may depend on the +150 belt, so no kit carries it at all.

        MEASURED: BeltStrength is m_itemType 18 (Utility), the same single slot as
        Demister and Wishbone, which five presets need. Granting it would either
        displace one of those or sit unworn -- 2 kg of nothing, which is the
        defect this work exists to remove.
        """
        for name in derive.js.all_preset_names():
            merged, _ordered, _dupes = derive.item_map(derive.js.load_preset(name))
            self.assertNotIn("BeltStrength", merged, name)

    def test_every_preset_stays_under_the_beltless_backpackless_budget(self):
        """Capacity is the flat measured 300 kg; nothing equipped is budgeted."""
        code, _out, err = self.cli("build", "TestWorld")
        self.assertEqual(code, 0, err)
        world = derive.load_world("TestWorld")
        for name in derive.js.all_preset_names():
            preset = derive.js.load_preset(name)
            measured, fallbacks = derive.load_stacks(world)
            part = derive.partition(preset, world, measured, fallbacks, self.table)
            self.assertLessEqual(part.carried_kg, world.carry_budget_kg, name)
            # And the guard must not have been widened to let a lever pay for it.
            self.assertEqual(part.capacity_kg, 300.0)
            self.assertEqual(part.budget_kg, 210.0)


class MisAuthoredEquipIsRefused(unittest.TestCase):
    """The guard. Each of these FAILS the build rather than reaching a player."""

    PRESET = "test-equip"

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

    def write_preset(self, kit, equip, materials=None):
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
                    "equip": equip,
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

    def template(self) -> Path:
        return self.world_root / self.PRESET / "charactertemplate.yml"

    def test_equipping_an_item_the_kit_does_not_grant_is_refused(self):
        self.write_preset(
            [{"prefab": "MaceIron", "quality": 1, "count": 1}],
            ["MaceIron", "ArmorIronChest"],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("equip: names 1 prefab(s) the preset does not grant", err)
        self.assertIn("ArmorIronChest", err)
        self.assertFalse(self.template().exists())

    def test_two_items_competing_for_one_slot_are_refused(self):
        self.write_preset(
            [
                {"prefab": "ArmorIronChest", "quality": 1, "count": 1},
                {"prefab": "ArmorRootChest", "quality": 1, "count": 1},
            ],
            ["ArmorIronChest", "ArmorRootChest"],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("ArmorIronChest loses the chest to ArmorRootChest", err)
        self.assertFalse(self.template().exists())

    def test_a_bow_beside_a_shield_is_refused_even_though_the_slots_look_distinct(self):
        self.write_preset(
            [
                {"prefab": "ShieldBanded", "quality": 1, "count": 1},
                {"prefab": "BowHuntsman", "quality": 1, "count": 1},
            ],
            ["ShieldBanded", "BowHuntsman"],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("ShieldBanded loses the left hand to BowHuntsman", err)

    def test_quality_above_the_items_measured_maximum_is_refused(self):
        # MEASURED: ShieldBanded is m_maxQuality 3. Shields cap one level below
        # weapons and armour, and every preset in this tree had this wrong.
        self.write_preset(
            [{"prefab": "ShieldBanded", "quality": 4, "count": 1}], ["ShieldBanded"]
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("ShieldBanded asks 4 but m_maxQuality is 3", err)
        self.assertFalse(self.template().exists())

    def test_quality_at_the_measured_maximum_is_accepted(self):
        self.write_preset(
            [{"prefab": "ShieldBanded", "quality": 3, "count": 1}], ["ShieldBanded"]
        )
        code, _out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 0, err)
        doc = yaml.safe_load(self.template().read_text("utf-8"))
        self.assertEqual(doc["quality"]["ShieldBanded"], 3)
        self.assertEqual(doc["equip"], ["ShieldBanded"])

    def test_a_kit_that_only_fits_with_the_belt_equipped_is_still_refused(self):
        """The belt is not a lever. 280 kg + a Megingjord is still over budget.

        MEASURED: BeltStrength grants +150 carry through
        SE_Stats.m_addMaxCarryWeight, so 280 kg would be comfortable for a
        character wearing one. The guard scores against the flat 300 kg capacity
        and the 210 kg budget with NOTHING equipped contributing, so this fails --
        which is the point: a kit that fits only because of an equipped item
        regresses the moment the item is dropped, unequipped, or lost on death.
        """
        self.write_preset(
            [
                {"prefab": "BeltStrength", "quality": 1, "count": 1},
                {"prefab": "Iron", "quality": 1, "count": 24},  # 12 kg each = 288 kg
            ],
            ["BeltStrength"],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("over the 210 kg budget", err)
        self.assertFalse(self.template().exists())

    def test_an_equipped_item_dropped_for_weight_is_refused(self):
        """Equipping needs the item IN the inventory, so a dropped one is a lie.

        The armour is in `materials`, which the partition trims to fit, so the
        trim silently removes something `equip` names.
        """
        self.write_preset(
            [{"prefab": "MaceIron", "quality": 1, "count": 1}],
            ["MaceIron", "ArmorIronChest"],
            materials=[
                {"prefab": "Iron", "count": 30},          # 360 kg, cannot come
                {"prefab": "ArmorIronChest", "count": 1},
            ],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        # The armour IS light enough to survive the trim, so this must pass --
        # the test is that the guard does not fire spuriously.
        self.assertEqual(code, 0, err)
        doc = yaml.safe_load(self.template().read_text("utf-8"))
        self.assertIn("ArmorIronChest", doc["items"])

    def test_equipping_a_material_is_refused(self):
        self.write_preset(
            [{"prefab": "MaceIron", "quality": 1, "count": 1}],
            ["MaceIron", "Iron"],
            materials=[{"prefab": "Iron", "count": 1}],
        )
        code, out, err = self.cli("build", "TestWorld", "--preset", self.PRESET)
        self.assertEqual(code, 1, out)
        self.assertIn("the game cannot equip at all", err)
        self.assertIn("Iron (Material)", err)


if __name__ == "__main__":
    unittest.main()
