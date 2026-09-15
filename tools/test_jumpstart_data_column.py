#!/usr/bin/env python3
"""Tests for the blueprint data column and the `spawn_object` emitter.

Three defects are guarded here, and all three are the same shape: a computation
that confidently answers a question it is not measuring.

1. **Argument order.** `spawn_object` does NOT take the argument order vanilla
   `spawn` takes, and it does not use the same order for its own two vector
   parameters. MEASURED from the IL of the deployed Server Devcommands:

     `pos=` -> `Parse::VectorZXYRange`: x = token 1, y = token 2, z = token 0.
     `rot=` -> `Parse::VectorYXZRange`: x = token 1, y = token 0, z = token 2.
     `from=` -> `Parse::VectorXZY`:     x = token 0, y = token 2, z = token 1.

   Three orders in one command. The `" (vec x,z,y)"` help text belongs to
   `VectorXZY`, i.e. to `from=`, and reading it as documentation for `pos=`
   produced a plan that placed a whole 1,090-piece settlement with X and Z
   exchanged -- confirmed against the saved ZDOs, which is how it was caught.
   A transposition here produces no error at all: the building simply lands
   somewhere else, or rotated. Every fixture below is deliberately ASYMMETRIC:
   a position like `(5, 5, 5)` or a yaw-only rotation passes under any
   permutation and proves nothing.

2. **Key/value order inside a `DataEntry`.** The payload is `(int32 hash,
   value)` pairs, and `entry.floats[r.i32()] = r.f32()` reads them in the WRONG
   order, because Python evaluates an assignment's right-hand side before the
   subscript in its target. With fixed-width values the cursor stays aligned, so
   the decode "succeeds" with every key and value swapped and `-1.0f` appears as
   a key hash. `test_keys_are_not_transposed_with_values` is the guard.

3. **Section order inside a `DataEntry`.** Longs (flag 0x40) are read BEFORE
   strings (0x10), which is not bit order.

Plus two content contracts: the data column is passed through byte-for-byte, and
the 4 corpus rows whose column 13 is the literal item name `ShieldWood` are
translated rather than dropped.
"""

import base64
import struct
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BLUEPRINTS = TOOLS / "jumpstart" / "blueprints"
sys.path.insert(0, str(BLUEPRINTS))

import data_entry as de  # noqa: E402
import to_rcon_plan as trp  # noqa: E402

HEADER = "#Name:fixture\n#Creator:test\n#Description:fixture\n#Category:test\n#Pieces\n"


def row(prefab, pos, rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0), data="", info=""):
    x, y, z = pos
    rx, ry, rz, rw = rot
    sx, sy, sz = scale
    return (f"{prefab};;{x};{y};{z};{rx};{ry};{rz};{rw};{info};"
            f"{sx};{sy};{sz};{data}")


def plan(rows, *, extra=()):
    """Emit a plan for `rows` and return its command lines."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "fixture.blueprint"
        src.write_text(HEADER + "\n".join(rows) + "\n", "utf-8")
        out = Path(tmp) / "plan.txt"
        rc = trp.main([str(src), "--at", "1000", "200", "-3000",
                       "--align", "raw", "--keep-loot", "--keep-creatures",
                       "--keep-forage", "--out", str(out), *extra])
        assert rc == 0, f"emitter returned {rc}"
        return out.read_text("utf-8").splitlines()


def arg(line, key):
    for token in line.split(" "):
        if token.startswith(key + "="):
            return token[len(key) + 1:]
    return None


class ArgumentOrder(unittest.TestCase):
    """`pos=z,x,y`, `rot=y,x,z`, `from=` pinned -- fixtures where a swap shows."""

    def test_pos_is_z_x_y(self) -> None:
        # Three distinct local offsets and three distinct --at components, so
        # all nine possible world components are distinct numbers and no
        # permutation can coincide with another.
        line = plan([row("wood_floor", (11.0, 22.0, 33.0))])[0]
        # world (x, y, z) = local + --at = (1011, 222, -2967), and the tokens
        # are z, x, y -- MEASURED from Parse::VectorZXYRange and confirmed
        # against a saved ZDO.
        self.assertEqual(arg(line, "pos"), "-2967,1011,222")

    def test_the_position_reference_is_pinned_to_the_origin(self) -> None:
        # `pos=` is relative to `From`, and `From` defaults to a PLAYER's
        # position when one is resolvable. Without this the same plan lands
        # somewhere else as soon as anybody logs in.
        line = plan([row("wood_floor", (11.0, 22.0, 33.0))])[0]
        self.assertEqual(arg(line, "from"), "0,0,0")

    def test_rot_is_y_x_z_not_x_y_z(self) -> None:
        # A rotation about one axis only would be indistinguishable under a
        # component swap; use three different angles.
        quat = trp.euler_to_quat(10.0, 40.0, 70.0)
        line = plan([row("wood_floor", (0.0, 0.0, 0.0), rot=quat)])[0]
        ex, ey, ez = trp.quat_to_euler(*quat)
        self.assertEqual(arg(line, "rot"),
                         f"{trp.fmt(ey)},{trp.fmt(ex)},{trp.fmt(ez)}")
        # and state it as literals too, so the test does not simply restate the
        # implementation it is checking
        self.assertEqual(arg(line, "rot"), "40,10,70")

    def test_the_command_is_spawn_object_not_spawn(self) -> None:
        line = plan([row("wood_floor", (1.0, 2.0, 3.0))])[0]
        self.assertTrue(line.startswith("spawn_object wood_floor "), line)
        # vanilla `spawn` takes bare positional coordinates; `spawn_object` does
        # not, and mixing the two silently spawns at the origin
        self.assertNotIn(" -rotation ", line)


class DataPassThrough(unittest.TestCase):
    def setUp(self) -> None:
        entry = de.DataEntry()
        entry.floats[de.stable_hash("health")] = -1.0
        entry.floats[de.stable_hash("support")] = 53.25
        entry.ints[de.stable_hash("HasFields")] = 1
        entry.longs[de.stable_hash("creator")] = 1234567890123
        entry.strings[de.stable_hash("steamName")] = "Olivander"
        self.payload = entry.encode()

    def test_the_column_is_emitted_byte_for_byte(self) -> None:
        line = plan([row("wood_floor", (1.0, 2.0, 3.0), data=self.payload)])[0]
        self.assertEqual(arg(line, "data"), self.payload)

    def test_a_row_without_a_column_gets_no_data_argument(self) -> None:
        line = plan([row("wood_floor", (1.0, 2.0, 3.0))])[0]
        self.assertIsNone(arg(line, "data"))

    def test_strip_identity_removes_the_third_party_keys_and_keeps_the_rest(self) -> None:
        line = plan([row("wood_floor", (1.0, 2.0, 3.0), data=self.payload)],
                    extra=("--strip-identity",))
        got = de.decode(arg(line[0], "data"))
        self.assertIsNone(got.get_string("steamName"))
        self.assertIsNone(got.get_long("creator"))
        self.assertEqual(got.get_float("health"), -1.0)
        self.assertEqual(got.get_int("HasFields"), 1)

    def test_no_data_discards_the_column(self) -> None:
        line = plan([row("wood_floor", (1.0, 2.0, 3.0), data=self.payload)],
                    extra=("--no-data",))
        self.assertIsNone(arg(line[0], "data"))

    def test_a_scale_only_row_carries_its_scale(self) -> None:
        line = plan([row("wood_floor", (1.0, 2.0, 3.0), scale=(2.0, 3.0, 4.0))])[0]
        got = de.decode(arg(line, "data"))
        self.assertEqual(got.get_vec("scale"), (2.0, 3.0, 4.0))


class PlainItemStandRows(unittest.TestCase):
    """The 4 corpus rows whose column 13 is `ShieldWood`, not a payload.

    They are NOT a base64 problem: every character of `ShieldWood` is in the
    base64 alphabet, so a base64 guard passes them and then the DataEntry reader
    runs off the end of the buffer.
    """

    def test_the_literal_decodes_as_base64_but_is_not_a_data_entry(self) -> None:
        self.assertEqual(len(de.b64_bytes("ShieldWood")), 7)
        with self.assertRaises(de.DataEntryError):
            de.decode("ShieldWood")

    def test_the_item_name_is_carried_rather_than_dropped(self) -> None:
        line = plan([row("itemstand", (1.0, 2.0, 3.0), data="ShieldWood")])[0]
        blob = arg(line, "data")
        self.assertIsNotNone(blob, "the row must still carry its item")
        self.assertEqual(de.decode(blob).get_string("item"), "ShieldWood")

    def test_the_row_is_still_emitted(self) -> None:
        lines = plan([row("itemstand", (1.0, 2.0, 3.0), data="ShieldWood")])
        self.assertEqual(len(lines), 1)


class TerrainHeaderPieces(unittest.TestCase):
    """Piece rows written under a `#Terrain` header, which Infinity Hammer drops.

    MEASURED: the catalogued corpus holds 463 such piece rows against 16 genuine
    terrain operations, and `BlackForestRaiderTown1` loses 187 of its 1,090 rows
    to the discard -- all props, no terrain.
    """

    def test_a_piece_row_under_a_terrain_header_is_emitted(self) -> None:
        body = [row("wood_floor", (0.0, 0.0, 0.0)),
                "#Terrain",
                row("FirTree_oldLog", (5.0, 6.0, 7.0))]
        lines = plan(body)
        self.assertEqual([line.split(" ")[1] for line in lines],
                         ["wood_floor", "FirTree_oldLog"])

    def test_a_genuine_terrain_operation_is_never_emitted(self) -> None:
        # PlanBuild writes `shape;x;y;z;radius;rotation;smooth;` -- 8 fields.
        # `circle` and `square` both resolve `vanilla` in the prefab evidence,
        # so nothing downstream would catch `spawn_object circle`.
        body = [row("wood_floor", (0.0, 0.0, 0.0)),
                "#Terrain",
                "circle;10;0;10;8;0;1;",
                "square;20;0;20;4;0;0;"]
        lines = plan(body)
        self.assertEqual([line.split(" ")[1] for line in lines], ["wood_floor"])

    def test_other_unknown_headers_still_discard_their_rows(self) -> None:
        body = [row("wood_floor", (0.0, 0.0, 0.0)),
                "#FuturePieceData:v1",
                row("wood_wall", (5.0, 6.0, 7.0))]
        lines = plan(body)
        self.assertEqual([line.split(" ")[1] for line in lines], ["wood_floor"])


class DataEntryWireFormat(unittest.TestCase):
    def test_keys_are_not_transposed_with_values(self) -> None:
        entry = de.DataEntry()
        entry.floats[de.stable_hash("health")] = -1.0
        got = de.decode(entry.encode())
        self.assertEqual(got.get_float("health"), -1.0)
        # the transposed read puts the float's bit pattern in the key position
        self.assertNotIn(struct.unpack("<i", struct.pack("<f", -1.0))[0], got.floats)

    def test_longs_are_read_before_strings(self) -> None:
        long_hash = de.stable_hash("creator")
        str_hash = de.stable_hash("steamName")
        longs = struct.pack("<Bi q", 1, long_hash, -99)
        strings = struct.pack("<Bi", 1, str_hash) + b"\x03abc"
        flags = struct.pack("<i", de.FLAG_LONGS | de.FLAG_STRINGS)

        right = de.decode(base64.b64encode(flags + longs + strings).decode())
        self.assertEqual(right.get_long("creator"), -99)
        self.assertEqual(right.get_string("steamName"), "abc")

        # bit order -- strings first -- must not parse, or the decoder is not
        # order-sensitive and a wrong-order decoder would go unnoticed
        with self.assertRaises(de.DataEntryError):
            de.decode(base64.b64encode(flags + strings + longs).decode())

    def test_a_payload_with_trailing_bytes_is_refused(self) -> None:
        entry = de.DataEntry()
        entry.ints[de.stable_hash("HasFields")] = 1
        raw = de.b64_bytes(entry.encode())
        with self.assertRaises(de.DataEntryError):
            de.decode(base64.b64encode(raw + b"\x00").decode())

    def test_stable_hash_matches_the_hashes_the_corpus_carries(self) -> None:
        # These four hashes are read straight out of real corpus payloads, so a
        # wrong hash function cannot satisfy them.
        self.assertEqual(de.stable_hash("support") & 0xFFFFFFFF, 0x2646AE77)
        self.assertEqual(de.stable_hash("health") & 0xFFFFFFFF, 0xCB5E26CC)
        self.assertEqual(de.stable_hash("WearNTear.m_health") & 0xFFFFFFFF, 0xAECEF0AD)
        self.assertEqual(de.stable_hash("items") & 0xFFFFFFFF, 0xC80A10C6)

    def test_an_inventory_decodes_item_for_item(self) -> None:
        # A real corpus payload: a `TreasureChest_meadows` from
        # BlackForestRaiderTown1 holding flint arrows, feathers and amber.
        blob = (
            "GQAAAAOt8M6uAACAv8wmXssAAIC/d65GJmIJVUIEVxF/7QEAAAD0j4X8AAAAAIn1F3YB"
            "AAAAZrPdfgEAAAABxhAKyNABYWdBQUFBTUFBQUFGVkc5eVkyZ0JBQUFBQUFDZ1FRQUFB"
            "QUFBQUFBQUFBRUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUJVRnRZbVZ5"
            "QVFBQUFBQUF5RUlBQUFBQUFRQUFBQUFCQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB"
            "QUFBQUFBQVZEYjJsdWN3WUFBQUFBQU1oQ0FRQUFBQUVBQUFBQUFRQUFBQUFBQUFBQUFB"
            "QUFBQUFBQUFBQUFBQUFBQUFBQUFBPQ=="
        )
        entry = de.decode(blob)
        version, items = de.inventory_of(entry)
        self.assertEqual(version, 106)
        self.assertEqual([(i.name, i.stack) for i in items],
                         [("Torch", 1), ("Amber", 1), ("Coins", 6)])
        self.assertEqual(entry.encode(), blob, "the payload must re-encode byte-exactly")


class Corpus(unittest.TestCase):
    """The agreement point with the sibling evaluation, on the real corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.bodies, _empty, _collisions = de.resolve_corpus()
        except SystemExit as exc:
            raise unittest.SkipTest(f"corpus unavailable: {exc}") from None
        if not cls.bodies:
            raise unittest.SkipTest("corpus unavailable")

    def test_every_data_column_decodes_except_the_four_plain_item_rows(self) -> None:
        bad = []
        for name, path in sorted(self.bodies.items()):
            # lint:per-frame bounded by the 157-body manifest; this is a test,
            # not a per-frame path.
            for prefab, value in de.data_columns(path):
                try:
                    de.decode(value)
                except de.DataEntryError:
                    bad.append((name, prefab, value))
        self.assertEqual({v for _n, _p, v in bad}, {"ShieldWood"})
        self.assertEqual(len(bad), 4, bad)
        self.assertEqual(sorted(p for _n, p, _v in bad), ["itemstand"] * 4)

    def test_round_trip_is_byte_exact_on_the_corpus(self) -> None:
        # This is the proof that licences `--strip-identity` and `--merge-scale`
        # to re-encode a payload at all.
        mismatched = []
        decoded = 0
        for name, path in sorted(self.bodies.items()):
            # lint:per-frame bounded by the 157-body manifest; this is a test,
            # not a per-frame path.
            for prefab, value in de.data_columns(path):
                try:
                    entry = de.decode(value)
                except de.DataEntryError:
                    continue
                decoded += 1
                if entry.encode() != value:
                    mismatched.append((name, prefab))
        self.assertGreater(decoded, 125_000, "corpus looks truncated")
        self.assertEqual(mismatched, [])


if __name__ == "__main__":
    unittest.main()
