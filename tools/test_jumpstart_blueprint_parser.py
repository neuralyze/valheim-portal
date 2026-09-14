#!/usr/bin/env python3
"""Tests for the blueprint section state machine.

The defect: `inventory.py` and `to_rcon_plan.py` kept the current section across
an unrecognised `#Header`. Infinity Hammer does not -- MEASURED from the
user-string heap of the DEPLOYED
`Ulfsland/data/bepinex/BepInEx/plugins/Infinity_Hammer/InfinityHammer.dll`
(`monodis --userstrings`), whose complete set of reader header literals is

    "#" "#name:" "#creator:" "#description:" "#category:" "#center:"
    "#coordinates:" "#rotation:" "#pieces" "#height:" "#paint:"

plus "#SnapPoints" / "#TerrainHeight:" / "#TerrainPaint:". There is no
"#objects" literal anywhere in the assembly. An unrecognised header sets the
section to None and its rows are discarded, so PlanBuild's `#Terrain` block --
rows shaped `shape;x;y;z;radius;rotation;smooth;` -- was being read as pieces
named `circle` and `square`. The bundle-token viability check cannot catch that
(both words are genuine tokens in the game's asset bundles, and both resolve
`vanilla` in `data/prefab_evidence.json`), so `to_rcon_plan.py` would have
emitted `spawn circle ...` at a live server.

Fixtures are Infinity Hammer's OWN parser-contract blueprints, vendored by the
sibling `BlueprintLibrary` under `tools/jumpstart/library/bodies/
JereKuusela__valheim-infinity_hammer/` from JereKuusela/valheim-infinity_hammer
@ ad56b79, Unlicense / public domain (LICENSE vendored beside them).
`terrain-future-section.blueprint` states the contract in its own description:
"Unknown sections must not inherit the preceding parser state".
"""

import sys
import tempfile
import unittest
from pathlib import Path

BLUEPRINTS = Path(__file__).resolve().parent / "jumpstart" / "blueprints"
FIXTURES = (
    Path(__file__).resolve().parent
    / "jumpstart"
    / "library"
    / "bodies"
    / "JereKuusela__valheim-infinity_hammer"
)
sys.path.insert(0, str(BLUEPRINTS))

import inventory  # noqa: E402
import to_rcon_plan  # noqa: E402

FUTURE = FIXTURES / "terrain-future-section.blueprint"
CONTRACT = FIXTURES / "terrain-blueprint-contract.blueprint"


class UnknownSections(unittest.TestCase):
    def setUp(self) -> None:
        if not FUTURE.exists():
            self.skipTest(f"fixture not vendored: {FUTURE}")

    def test_rows_under_an_unknown_header_are_not_pieces(self) -> None:
        got = inventory.parse_blueprint(FUTURE)
        # the fixture holds exactly one real piece and one decoy under
        # #FuturePieceData:v1
        self.assertEqual(got["pieces"], 1)
        self.assertEqual(list(got["prefab_counts"]), ["wood_floor"])
        self.assertNotIn("future_payload", got["prefab_counts"])

    def test_discarded_rows_are_counted_and_the_sections_named(self) -> None:
        got = inventory.parse_blueprint(FUTURE)
        # future_payload plus the 999;999 row under #FutureTerrainData:v1
        self.assertEqual(got["discarded_rows"], 2)
        self.assertEqual(
            got["unknown_sections"], ["#FuturePieceData", "#FutureTerrainData"]
        )

    def test_recognised_sections_still_accumulate_across_an_unknown_one(self) -> None:
        got = inventory.parse_blueprint(FUTURE)
        # 1;2 and 3;4, then 888;888 under the second #TerrainHeight: header
        self.assertEqual(got["terrain_height_rows"], 3)
        self.assertEqual(got["terrain_paint_rows"], 2)

    def test_converter_emits_no_object_for_a_discarded_row(self) -> None:
        objs = to_rcon_plan.read_objects(FUTURE)
        self.assertEqual([o.prefab for o in objs], ["wood_floor"])


class RecognisedSections(unittest.TestCase):
    def setUp(self) -> None:
        if not CONTRACT.exists():
            self.skipTest(f"fixture not vendored: {CONTRACT}")

    def test_terrain_sections_are_not_pieces_and_pieces_are_not_terrain(self) -> None:
        got = inventory.parse_blueprint(CONTRACT)
        self.assertEqual(got["pieces"], 2)
        self.assertEqual(got["prefab_counts"], {"wood_floor": 2})
        self.assertEqual(got["terrain_height_rows"], 2)
        self.assertEqual(got["terrain_paint_rows"], 2)
        self.assertEqual(got["discarded_rows"], 0)
        self.assertEqual(got["unknown_sections"], [])


class HeaderRules(unittest.TestCase):
    """The comment and legacy rules, which no vendored fixture exercises."""

    def _parse(self, body: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.blueprint"
            path.write_text(body, "utf-8")
            return inventory.parse_blueprint(path)

    def test_a_bare_hash_or_hash_space_is_a_comment_and_keeps_the_section(self) -> None:
        got = self._parse(
            "#Pieces\nwood_floor;;0;0;0;0;0;0;1;;1;1;1;\n"
            "#\n"
            "# a human note\n"
            "wood_wall;;1;0;0;0;0;0;1;;1;1;1;\n"
        )
        self.assertEqual(got["pieces"], 2)
        self.assertEqual(got["discarded_rows"], 0)

    def test_an_objects_header_is_unknown_because_the_dll_has_no_such_literal(self) -> None:
        got = self._parse(
            "#Pieces\nwood_floor;;0;0;0;0;0;0;1;;1;1;1;\n"
            "#Objects\nnot_a_piece;;9;9;9;0;0;0;1;;1;1;1;\n"
        )
        self.assertEqual(got["pieces"], 1)
        self.assertEqual(got["discarded_rows"], 1)
        self.assertEqual(got["unknown_sections"], ["#Objects"])

    def test_legacy_height_paint_sections_are_flagged_not_parsed(self) -> None:
        got = self._parse(
            "#Pieces\nwood_floor;;0;0;0;0;0;0;1;;1;1;1;\n#Height:1\n1;2;3\n"
        )
        self.assertTrue(got["legacy_terrain_format"])
        self.assertEqual(got["pieces"], 1)

    def test_converter_refuses_a_legacy_file_outright(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.blueprint"
            path.write_text(
                "#Pieces\nwood_floor;;0;0;0;0;0;0;1;;1;1;1;\n#Paint:1\n1;2;3\n", "utf-8"
            )
            with self.assertRaises(inventory.LegacyTerrainFormat):
                to_rcon_plan.read_objects(path)


class UnspawnablePrefabs(unittest.TestCase):
    def test_plan_is_refused_when_a_prefab_cannot_be_spawned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.blueprint"
            path.write_text(
                "#Pieces\nwood_floor;;0;0;0;0;0;0;1;;1;1;1;\n"
                "definitely_not_a_real_prefab;;1;0;0;0;0;0;1;;1;1;1;\n",
                "utf-8",
            )
            rc = to_rcon_plan.main([str(path), "--at", "0", "0", "0"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
