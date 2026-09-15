#!/usr/bin/env python3
"""Decode, re-encode and name the blueprint 14th column (`DataEntry`).

What this is
------------
Column 13 of a PlanBuild-format `#Pieces` row is base64 of
`WorldEditCommands.Data.DataEntry`.  Infinity Hammer writes it
(`infinity_hammer.cfg` ships `Save data to blueprints = true`) and World Edit
Commands 1.77.0 -- already deployed server-side -- reads it back through
`spawn_object <prefab> ... data=<base64>`: `Data.DataHelper::Get(name)` looks the
string up in `data.yaml` and, on a miss, feeds it to
`Data.DataEntry::.ctor(string)`, which is `new ZPackage(base64)` ->
`Load(ZPackage)`.  So this column is the per-piece ZDO state, and it is the
difference between a chest that lands empty and a chest that lands full.

Wire format, MEASURED from `Data.DataEntry::Load(class [assembly_valheim]ZPackage)`
in the deployed `World_Edit_Commands/WorldEditCommands.dll` 1.77.0 (`monodis`,
method at IL_0000..IL_0269).  An `int32` flag word, then one section per set bit,
each a `byte` count followed by `count x (int32 key-hash, value)`:

    int32 flags
    0x0001  floats      DataValue::Float      -> ZPackage::ReadSingle
    0x0002  vector3     DataValue::Vector3    -> ZPackage::ReadVector3   (3 x f32)
    0x0004  quaternion  DataValue::Quaternion -> ZPackage::ReadQuaternion (4 x f32)
    0x0008  ints        DataValue::Int        -> ZPackage::ReadInt
    0x0040  longs       DataValue::Long       -> ZPackage::ReadLong
    0x0010  strings     DataValue::String     -> ZPackage::ReadString
    0x0080  byteArrays  ZPackage::ReadByteArray   (int32 length, then bytes)
    0x0100  connection  byte ConnectionType, int32 hash   -- NOT length-prefixed
    0x0200  persistent  DataValue::Bool       -> ZPackage::ReadBool
    0x0400  distant     DataValue::Bool       -> ZPackage::ReadBool
    0x0800  priority    byte ZDO/ObjectType

**Longs (0x40) are read BEFORE strings (0x10).**  That is not bit order, and a
decoder that follows bit order parses 89 files of this corpus into plausible
nonsense instead of failing loudly.  The `consumed == len(payload)` assertion in
`decode` is what catches it: a wrong section order leaves the cursor in the wrong
place, and on real data it lands short or long of the buffer end.

Key names are `StringExtensionMethods::GetStableHashCode` of the key name.  The
hash is reproduced here and validated against the corpus rather than against a
disassembly: `KNOWN_KEYS` must resolve the high-frequency keys, and
`--survey` prints the unresolved remainder so a wrong hash is visible as a wall
of `0x...` instead of `support`/`health`/`items`.

Usage:
    data_entry.py --survey            # decode the whole catalogued corpus
    data_entry.py --survey --json OUT
    data_entry.py --dump <base64>     # one blob, named and typed
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent

FLAG_FLOATS = 0x0001
FLAG_VECS = 0x0002
FLAG_QUATS = 0x0004
FLAG_INTS = 0x0008
FLAG_STRINGS = 0x0010
FLAG_LONGS = 0x0040
FLAG_BYTE_ARRAYS = 0x0080
FLAG_CONNECTION = 0x0100
FLAG_PERSISTENT = 0x0200
FLAG_DISTANT = 0x0400
FLAG_PRIORITY = 0x0800

# The key vocabulary this corpus actually uses, 160 names covering 117 of the
# 172 distinct hashes observed and all but 2,024 of the ~1.3 M key occurrences.
# DERIVED, not guessed: every name here hashes to a key that the corpus really
# carries. Candidates came from the `ldstr` user strings of the deployed
# `assembly_valheim.dll`, from `<Type>.<m_field>` pairs built out of its own
# class and field tables (that is the shape Infinity Hammer writes component
# overrides in), from `HasFields<Type>` (the shape it writes component-present
# markers in), and from the user strings of the deployed `WorldEditCommands.dll`
# and `InfinityHammer.dll`. Anything still unmatched is reported as a hex
# literal by `--survey` rather than being assigned a plausible name.
KEY_NAMES: tuple[str, ...] = (
    "0_crafterName", "0_item", "1_crafterName", "1_item", "2_crafterName",
    "2_item", "4_crafterName", "5_crafterName", "5_item", "BodyVelocity",
    "CL&LC effect", "CL&LC infusion", "ChestItem", "Content",
    "Destructible.m_health", "Destructible.m_minToolTier",
    "Fireplace.m_infiniteFuel", "HasFields", "HasFieldsDestructible",
    "HasFieldsFireplace", "HasFieldsHumanoid", "HasFieldsImpactEffect",
    "HasFieldsLightFlicker", "HasFieldsMineRock5", "HasFieldsMonsterAI",
    "HasFieldsOfferingBowl", "HasFieldsPickable", "HasFieldsPiece",
    "HasFieldsStaticTarget", "HasFieldsTerrainModifier", "HasFieldsTreeBase",
    "HasFieldsWearNTear", "HelmetItem", "Humanoid.m_canSwim",
    "Humanoid.m_flying", "Humanoid.m_group", "Humanoid.m_name",
    "Humanoid.m_runSpeed", "Humanoid.m_staggerWhenBlocked",
    "Humanoid.m_swimSpeed", "Humanoid.m_swimTurnSpeed",
    "ImpactEffect.m_minVelocity", "InUse", "LeftItem", "LeftItemVariant",
    "LegItem", "LightFlicker.m_flickerIntensity", "LightFlicker.m_flickerSpeed",
    "LightFlicker.m_ttl", "LookTarget", "MineRock5.m_minToolTier",
    "MonsterAI.m_circleTargetDistance", "MonsterAI.m_circleTargetDuration",
    "MonsterAI.m_circleTargetInterval", "MonsterAI.m_circulateWhileCharging",
    "MonsterAI.m_hearRange", "MonsterAI.m_minAttackInterval",
    "MonsterAI.m_randomCircleInterval", "MonsterAI.m_randomMoveInterval",
    "MonsterAI.m_sleeping", "MonsterAI.m_viewAngle", "MonsterAI.m_viewRange",
    "MonsterAI.m_wakeupRange", "OfferingBowl.m_bossItem",
    "OfferingBowl.m_bossItems", "OfferingBowl.m_bossPrefab",
    "OfferingBowl.m_enableSolidHeightCheck", "OfferingBowl.m_name",
    "OfferingBowl.m_spawnAreaOffset", "OfferingBowl.m_spawnBossDelay",
    "OfferingBowl.m_spawnBossMaxDistance", "OfferingBowl.m_spawnBossMinDistance",
    "OfferingBowl.m_useItemText", "OfferingBowl.m_usedAltarText",
    "OfferingBowl.m_wrongOfferText", "Pickable.m_itemPrefab",
    "Piece.m_primaryTarget", "Piece.m_randomTarget", "RandMatSeed",
    "RandomSkillFactor", "RightItem", "ShoulderItem", "ShoulderItemVariant",
    "StartTime", "StaticTarget.m_primaryTarget", "StaticTarget.m_randomTarget",
    "TerrainModifier.m_levelRadius", "TerrainModifier.m_paintRadius",
    "TerrainModifier.m_smoothRadius", "TreeBase.m_minToolTier", "UtilityItem",
    "WearNTear.m_health", "WearNTear.m_noSupportWear", "WearNTear.m_supports",
    "accTime", "addedDefaultItems", "alive_time", "author", "bakeTimer",
    "body_avel", "body_vel", "crafterID", "crafterName", "creator",
    "creatorName", "dataCount", "durability", "enabled", "fuel", "haveTarget",
    "health", "item", "items", "landed", "lastTime", "lastWorldTime", "level",
    "location", "max_health", "noise", "override_component",
    "override_interact", "override_spawnarea_spawn", "override_wear", "owner",
    "ownerName", "patrol", "patrolPoint", "permitted", "pickedUp", "piece",
    "plantTime", "pose", "product", "pu_id0", "pu_id1", "pu_name0", "pu_name1",
    "quality", "scale", "scaleScalar", "seAttrib", "seed", "slot0", "slot1",
    "spawnpoint", "spawntime", "stack", "state", "steamID", "steamName",
    "support", "tag", "tagauthor", "text", "tiltrot", "variant", "vel",
    "worldLevel", "xray_created",
)
STABLE_HASH_MASK = 0xFFFFFFFF


def stable_hash(text: str) -> int:
    """`StringExtensionMethods::GetStableHashCode`, returning a signed int32.

    Interleaved double-DJB2: even-indexed characters into one accumulator,
    odd-indexed into the other, combined as `a + b * 1566083941`. Validated
    against this corpus, not against documentation -- `--survey` names 99%+ of
    the observed keys, which a wrong hash cannot do.
    """
    a = b = 5381
    n = len(text)
    i = 0
    while i < n:
        ch = ord(text[i])
        if ch == 0:
            break
        a = (((a << 5) + a) & STABLE_HASH_MASK) ^ ch
        if i + 1 >= n:
            break
        nxt = ord(text[i + 1])
        if nxt == 0:
            break
        b = (((b << 5) + b) & STABLE_HASH_MASK) ^ nxt
        i += 2
    combined = (a + b * 1566083941) & STABLE_HASH_MASK
    return combined - 0x100000000 if combined >= 0x80000000 else combined


HASH_TO_NAME: dict[int, str] = {stable_hash(name): name for name in KEY_NAMES}

# Keys that identify a third party rather than describing the building. 8,620
# corpus rows carry a stranger's Steam ID and display name; there is no reason
# to write those into our world save. Stripping them re-encodes the payload, so
# it is opt-in and guarded by the byte-exact round-trip proof in
# `test_jumpstart_data_column.py`.
IDENTITY_KEYS: tuple[str, ...] = (
    "steamID", "steamName", "creator", "crafterID", "crafterName", "xray_created",
)
IDENTITY_HASHES: frozenset[int] = frozenset(stable_hash(k) for k in IDENTITY_KEYS)


class DataEntryError(ValueError):
    """The column is not a `DataEntry`.

    The 4 corpus rows that hit this are `itemstand` rows whose column 13 holds
    the literal item name (`ShieldWood`) instead of a payload. Note that they
    ARE base64-decodable -- every character of `ShieldWood` is in the base64
    alphabet -- so a base64 guard does not catch them; they decode to 7 bytes
    and then run off the end of the first section.
    """


class _Reader:
    """`ZPackage` read side, little-endian, with a cursor we can assert on."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def _take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.buf):
            raise DataEntryError(
                f"truncated: wanted {n} byte(s) at offset {self.pos}, "
                f"buffer is {len(self.buf)}"
            )
        out = self.buf[self.pos:end]
        self.pos = end
        return out

    def byte(self) -> int:
        return self._take(1)[0]

    def i32(self) -> int:
        return struct.unpack("<i", self._take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self._take(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self._take(4))[0]

    def boolean(self) -> bool:
        return self._take(1)[0] != 0

    def text(self) -> str:
        """`BinaryReader::ReadString`: 7-bit-encoded length, then UTF-8."""
        length = 0
        shift = 0
        while True:
            if shift > 35:
                raise DataEntryError("string length varint too long")
            piece = self.byte()
            length |= (piece & 0x7F) << shift
            if not piece & 0x80:
                break
            shift += 7
        raw = self._take(length)
        try:
            return raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            # `BinaryWriter::Write(string)` emits well-formed UTF-8, so bytes
            # that are not UTF-8 mean the cursor is not on a string at all --
            # i.e. this buffer is not a DataEntry. Surface it as such rather
            # than letting a UnicodeDecodeError escape and be read as a bug in
            # the survey.
            raise DataEntryError(f"string at offset {self.pos - length} is not "
                                 f"UTF-8: {exc}") from None

    def byte_array(self) -> bytes:
        length = self.i32()
        if length < 0:
            raise DataEntryError(f"negative byte-array length {length}")
        return self._take(length)


class _Writer:
    __slots__ = ("parts",)

    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def byte(self, v: int) -> None:
        self.parts.append(bytes((v & 0xFF,)))

    def i32(self, v: int) -> None:
        self.parts.append(struct.pack("<i", v))

    def i64(self, v: int) -> None:
        self.parts.append(struct.pack("<q", v))

    def f32(self, v: float) -> None:
        self.parts.append(struct.pack("<f", v))

    def boolean(self, v: bool) -> None:
        self.parts.append(b"\x01" if v else b"\x00")

    def text(self, v: str) -> None:
        raw = v.encode("utf-8")
        length = len(raw)
        while True:
            chunk = length & 0x7F
            length >>= 7
            self.parts.append(bytes((chunk | (0x80 if length else 0),)))
            if not length:
                break
        self.parts.append(raw)

    def byte_array(self, v: bytes) -> None:
        self.i32(len(v))
        self.parts.append(v)

    def out(self) -> bytes:
        return b"".join(self.parts)


@dataclass
class DataEntry:
    """One decoded column-13 payload.

    Dicts are keyed by the stable hash and hold insertion order, which is the
    file order; `encode` replays that order so a decode/encode round trip is
    byte-identical. `flags` is kept verbatim so a section that was flagged with
    a zero count re-encodes with the bit still set.
    """

    flags: int = 0
    floats: dict[int, float] = field(default_factory=dict)
    vecs: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    quats: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)
    ints: dict[int, int] = field(default_factory=dict)
    longs: dict[int, int] = field(default_factory=dict)
    strings: dict[int, str] = field(default_factory=dict)
    byte_arrays: dict[int, bytes] = field(default_factory=dict)
    connection: tuple[int, int] | None = None
    persistent: bool | None = None
    distant: bool | None = None
    priority: int | None = None

    def get_float(self, name: str) -> float | None:
        return self.floats.get(stable_hash(name))

    def get_int(self, name: str) -> int | None:
        return self.ints.get(stable_hash(name))

    def get_long(self, name: str) -> int | None:
        return self.longs.get(stable_hash(name))

    def get_string(self, name: str) -> str | None:
        return self.strings.get(stable_hash(name))

    def get_vec(self, name: str) -> tuple[float, float, float] | None:
        return self.vecs.get(stable_hash(name))

    def key_names(self) -> list[str]:
        names = []
        for section in (self.floats, self.vecs, self.quats, self.ints,
                        self.longs, self.strings, self.byte_arrays):
            for h in section:
                names.append(HASH_TO_NAME.get(h, f"0x{h & 0xFFFFFFFF:08x}"))
        return names

    def without_identity(self) -> DataEntry:
        """A copy with the Steam/creator/crafter keys removed.

        Re-encoding is only safe because `encode` is proven byte-exact on the
        whole corpus; see `test_round_trip_is_byte_exact_on_the_corpus`.
        """
        keep = lambda d: {k: v for k, v in d.items() if k not in IDENTITY_HASHES}
        clone = DataEntry(
            flags=0,
            floats=keep(self.floats),
            vecs=keep(self.vecs),
            quats=keep(self.quats),
            ints=keep(self.ints),
            longs=keep(self.longs),
            strings=keep(self.strings),
            byte_arrays=keep(self.byte_arrays),
            connection=self.connection,
            persistent=self.persistent,
            distant=self.distant,
            priority=self.priority,
        )
        # Preserve any bit the original set for a section we did not empty, so
        # a flagged-but-zero-count section survives.
        clone.flags = self.flags & ~_dirty_bits(self, clone)
        return clone

    def encode(self) -> str:
        """Base64 the game will accept, byte-identical to the input on decode."""
        w = _Writer()
        flags = self.flags
        for bit, section in (
            (FLAG_FLOATS, self.floats),
            (FLAG_VECS, self.vecs),
            (FLAG_QUATS, self.quats),
            (FLAG_INTS, self.ints),
            (FLAG_LONGS, self.longs),
            (FLAG_STRINGS, self.strings),
            (FLAG_BYTE_ARRAYS, self.byte_arrays),
        ):
            if section:
                flags |= bit
        if self.connection is not None:
            flags |= FLAG_CONNECTION
        if self.persistent is not None:
            flags |= FLAG_PERSISTENT
        if self.distant is not None:
            flags |= FLAG_DISTANT
        if self.priority is not None:
            flags |= FLAG_PRIORITY
        w.i32(flags)
        if flags & FLAG_FLOATS:
            w.byte(len(self.floats))
            for h, v in self.floats.items():
                w.i32(h)
                w.f32(v)
        if flags & FLAG_VECS:
            w.byte(len(self.vecs))
            for h, vec in self.vecs.items():
                w.i32(h)
                for c in vec:
                    w.f32(c)
        if flags & FLAG_QUATS:
            w.byte(len(self.quats))
            for h, quat in self.quats.items():
                w.i32(h)
                for c in quat:
                    w.f32(c)
        if flags & FLAG_INTS:
            w.byte(len(self.ints))
            for h, v in self.ints.items():
                w.i32(h)
                w.i32(v)
        if flags & FLAG_LONGS:
            w.byte(len(self.longs))
            for h, v in self.longs.items():
                w.i32(h)
                w.i64(v)
        if flags & FLAG_STRINGS:
            w.byte(len(self.strings))
            for h, v in self.strings.items():
                w.i32(h)
                w.text(v)
        if flags & FLAG_BYTE_ARRAYS:
            w.byte(len(self.byte_arrays))
            for h, v in self.byte_arrays.items():
                w.i32(h)
                w.byte_array(v)
        if flags & FLAG_CONNECTION:
            kind, h = self.connection  # type: ignore[misc]
            w.byte(kind)
            w.i32(h)
        if flags & FLAG_PERSISTENT:
            w.boolean(bool(self.persistent))
        if flags & FLAG_DISTANT:
            w.boolean(bool(self.distant))
        if flags & FLAG_PRIORITY:
            w.byte(int(self.priority))  # type: ignore[arg-type]
        return base64.b64encode(w.out()).decode("ascii")


def _dirty_bits(before: DataEntry, after: DataEntry) -> int:
    """Flag bits whose section lost every member, so they must not be re-set."""
    bits = 0
    for bit, old, new in (
        (FLAG_FLOATS, before.floats, after.floats),
        (FLAG_VECS, before.vecs, after.vecs),
        (FLAG_QUATS, before.quats, after.quats),
        (FLAG_INTS, before.ints, after.ints),
        (FLAG_LONGS, before.longs, after.longs),
        (FLAG_STRINGS, before.strings, after.strings),
        (FLAG_BYTE_ARRAYS, before.byte_arrays, after.byte_arrays),
    ):
        if old and not new:
            bits |= bit
    return bits


def b64_bytes(value: str) -> bytes:
    """`Convert.FromBase64String`, tolerant of the missing tail padding.

    Padding is added rather than demanded because the four bad corpus rows are
    NOT a base64 problem and must not be misattributed as one: `ShieldWood` is
    10 base64-alphabet characters and decodes to 7 bytes. Rejecting it here
    would report "bad base64" for what is really "a plain item name in the data
    column", and the fix for those two things is different.
    """
    pad = (-len(value)) % 4
    try:
        return base64.b64decode(value + "=" * pad, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DataEntryError(f"not base64: {exc}") from None


def decode(value: str) -> DataEntry:
    """Decode one column-13 string, insisting the whole buffer is consumed.

    `consumed == len(payload)` is the real check. A DataEntry has no trailer and
    no length field, so a decoder with the wrong section order or a wrong value
    width still produces a populated object -- it just stops in the wrong place.
    """
    raw = b64_bytes(value)
    r = _Reader(raw)
    flags = r.i32()
    entry = DataEntry(flags=flags)
    # Every loop binds the key hash to a local BEFORE reading the value. The
    # obvious `entry.floats[r.i32()] = r.f32()` is WRONG and silently so:
    # Python evaluates an assignment's right-hand side before the subscript in
    # the target, so it reads value-then-hash. With float and int values that
    # is the same number of bytes, so the cursor stays aligned and the decode
    # "succeeds" with every key and value transposed -- `-1.0f` shows up as key
    # hash 0xbf800000. It only fails loudly at the first variable-width value.
    if flags & FLAG_FLOATS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.floats[key] = r.f32()
    if flags & FLAG_VECS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.vecs[key] = (r.f32(), r.f32(), r.f32())
    if flags & FLAG_QUATS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.quats[key] = (r.f32(), r.f32(), r.f32(), r.f32())
    if flags & FLAG_INTS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.ints[key] = r.i32()
    # Longs BEFORE strings. See the module docstring.
    if flags & FLAG_LONGS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.longs[key] = r.i64()
    if flags & FLAG_STRINGS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.strings[key] = r.text()
    if flags & FLAG_BYTE_ARRAYS:
        for _ in range(r.byte()):
            key = r.i32()
            entry.byte_arrays[key] = r.byte_array()
    if flags & FLAG_CONNECTION:
        kind = r.byte()
        entry.connection = (kind, r.i32())
    if flags & FLAG_PERSISTENT:
        entry.persistent = r.boolean()
    if flags & FLAG_DISTANT:
        entry.distant = r.boolean()
    if flags & FLAG_PRIORITY:
        entry.priority = r.byte()
    if r.pos != len(raw):
        raise DataEntryError(
            f"consumed {r.pos} of {len(raw)} byte(s); "
            f"{len(raw) - r.pos} trailing"
        )
    return entry


# --------------------------------------------------------------------------
# Container inventories
# --------------------------------------------------------------------------
@dataclass
class Item:
    name: str
    stack: int
    durability: float
    pos: tuple[int, int]
    equipped: bool
    quality: int = 1
    variant: int = 0
    crafter_id: int = 0
    crafter_name: str = ""
    custom: dict[str, str] = field(default_factory=dict)
    world_level: int = 0
    picked_up: bool = False


def decode_inventory(value: bytes | str) -> tuple[int, list[Item]]:
    """`Inventory::Load(ZPackage)` for the `items` key.

    MEASURED from `Inventory::Load` / `Inventory::LoadOld` in the deployed
    `assembly_valheim.dll` (Valheim l-1.0.12): version is an `int32`; at
    version >= 108 (0x6c) the count is a `ushort` and each item goes through
    `ItemDrop/ItemData::Load`, below that `LoadOld` reads an `int32` count and
    the flat field list below. The corpus writes version 106, so it is the
    `LoadOld` path -- the version-gated fields matter and are gated here, not
    assumed.
    """
    raw = b64_bytes(value) if isinstance(value, str) else value
    r = _Reader(raw)
    version = r.i32()
    if version >= 0x6C:
        raise DataEntryError(
            f"inventory version {version} uses ItemDrop.ItemData::Load, which this "
            f"reader does not model; the corpus writes 106"
        )
    count = r.i32()
    items: list[Item] = []
    for _ in range(count):
        it = Item(
            name=r.text(),
            stack=r.i32(),
            durability=r.f32(),
            pos=(r.i32(), r.i32()),
            equipped=r.boolean(),
        )
        if version >= 101:
            it.quality = r.i32()
        if version >= 102:
            it.variant = r.i32()
        if version >= 103:
            it.crafter_id = r.i64()
            it.crafter_name = r.text()
        if version >= 104:
            for _pair in range(r.i32()):
                key = r.text()
                it.custom[key] = r.text()
        if version >= 105:
            it.world_level = r.i32()
        if version >= 106:
            it.picked_up = r.boolean()
        if 107 <= version < 109:
            r.boolean()
        items.append(it)
    if r.pos != len(raw):
        raise DataEntryError(
            f"inventory consumed {r.pos} of {len(raw)} byte(s)"
        )
    return version, items


def inventory_of(entry: DataEntry) -> tuple[int, list[Item]] | None:
    """The `items` payload, whichever of the two shapes it took.

    MEASURED: the blueprint corpus writes `items` as a STRING holding base64;
    the game migrates it to a byteArray on the ZDO. Both are handled so the same
    reader works on a blueprint row and on a saved chunk.
    """
    h = stable_hash("items")
    if h in entry.byte_arrays:
        return decode_inventory(entry.byte_arrays[h])
    if h in entry.strings:
        return decode_inventory(entry.strings[h])
    return None


# --------------------------------------------------------------------------
# Corpus survey
# --------------------------------------------------------------------------
def data_columns(path: Path) -> list[tuple[str, str]]:
    """Every `(prefab, column-13)` pair in one body, SECTION-UNAWARE.

    Deliberately not the emitter's reader: this counts what the FILE holds, so
    the survey total is independent of which sections any tool chooses to
    honour. Rows under `#Terrain` are included, which is where 308 of the
    data-bearing rows live.
    """
    out: list[tuple[str, str]] = []
    for row in path.read_text("utf-8", "replace").splitlines():
        if not row or row.startswith("#"):
            continue
        line = row.replace(",", ".") if "," in row else row
        p = line.split(";")
        if len(p) < 14 or not p[0].strip() or not p[13].strip():
            continue
        out.append((p[0].strip(), p[13].strip()))
    return out


def resolve_corpus() -> tuple[dict[str, Path], list[str], dict[str, list[str]]]:
    """The `.blueprint` bodies to survey, plus what was excluded and why.

    `survey_datum.resolve_bodies` keys by FILE NAME, and the manifest has two
    names carried by more than one SHA-256, so a name-keyed resolution silently
    picks one building and drops the other. That is not a rounding error:
    `brokkr-the-cathedral.blueprint` exists as an 8,240-piece body with no data
    column at all and as an 8,269-piece body where every piece carries one, so
    which copy wins moves the corpus total by 8,269 rows. The collisions are
    returned rather than resolved, because there is no measurement that says
    which building the manifest meant.

    Zero-byte bodies are excluded here: 10 manifest names resolve to empty
    files, and counting them as surveyed bodies inflates the denominator while
    contributing nothing.
    """
    sys.path.insert(0, str(HERE))
    import survey_datum  # noqa: PLC0415  (import here: only the survey needs it)

    manifest = json.loads(
        (JUMPSTART / "library" / "data" / "library_manifest.json").read_text("utf-8")
    )
    shas: dict[str, set[str]] = {}
    for entry in manifest["entries"]:
        shas.setdefault(entry["name"], set()).add(entry["sha256"])
    collisions = {n: sorted(s) for n, s in shas.items() if len(s) > 1}

    bodies: dict[str, Path] = {}
    empty: list[str] = []
    for name, path in survey_datum.resolve_bodies().items():
        if path.suffix.lower() != ".blueprint":
            continue  # .vbuild is 8 fields throughout; it has no column 13
        if path.stat().st_size == 0:
            empty.append(name)
            continue
        bodies[name] = path
    return bodies, sorted(empty), collisions


def survey() -> dict:
    """Decode every data column in the catalogued corpus.

    The numbers this prints are the agreement point with the sibling
    BlueprintTooling evaluation (`docs/proposed/2026-09-15-blueprint-tooling-
    evaluation.md` §1.1): 125,576 of 125,580 decode AND consume exactly their
    byte length; the 4 failures are `itemstand` rows whose column 13 is the
    literal `ShieldWood`.
    """
    bodies, empty, collisions = resolve_corpus()
    total = ok = 0
    failures: list[dict] = []
    round_trip_bad: list[str] = []
    key_rows: dict[str, int] = {}
    unresolved: dict[int, int] = {}
    inventories: list[dict] = []
    for name, path in sorted(bodies.items()):
        # lint:per-frame bounded by the 164-body manifest; this is a one-shot
        # survey CLI, not a per-frame path.
        for prefab, value in data_columns(path):
            total += 1
            try:
                entry = decode(value)
            except DataEntryError as exc:
                failures.append({"body": name, "prefab": prefab,
                                 "value": value, "why": str(exc)})
                continue
            ok += 1
            if entry.encode() != value:
                round_trip_bad.append(f"{name}:{prefab}")
            for section in (entry.floats, entry.vecs, entry.quats, entry.ints,
                            entry.longs, entry.strings, entry.byte_arrays):
                for h in section:
                    label = HASH_TO_NAME.get(h)
                    if label is None:
                        unresolved[h] = unresolved.get(h, 0) + 1
                    else:
                        key_rows[label] = key_rows.get(label, 0) + 1
            inv = inventory_of(entry)
            if inv is not None:
                version, items = inv
                inventories.append({
                    "body": name, "prefab": prefab, "version": version,
                    "items": [f"{i.name} x{i.stack}" for i in items],
                })
    return {
        "bodies": len(bodies),
        "empty_bodies": empty,
        "name_collisions": collisions,
        "collision_rows": {
            n: len(data_columns(bodies[n])) for n in collisions if n in bodies
        },
        "data_rows": total,
        "decoded": ok,
        "failed": len(failures),
        "failures": failures,
        "round_trip_mismatches": round_trip_bad,
        "key_rows": dict(sorted(key_rows.items(), key=lambda kv: -kv[1])),
        "unresolved_hashes": {f"0x{h & 0xFFFFFFFF:08x}": c
                              for h, c in sorted(unresolved.items(), key=lambda kv: -kv[1])},
        "inventory_rows": len(inventories),
        "inventory_bodies": len({i["body"] for i in inventories}),
        "inventories": inventories,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--survey", action="store_true", help="decode the whole corpus")
    ap.add_argument("--json", help="write the survey here")
    ap.add_argument("--dump", metavar="BASE64", help="decode one blob and print it")
    args = ap.parse_args(argv)

    if args.dump:
        entry = decode(args.dump)
        for label, section in (("float", entry.floats), ("vec3", entry.vecs),
                               ("quat", entry.quats), ("int", entry.ints),
                               ("long", entry.longs), ("string", entry.strings),
                               ("bytes", entry.byte_arrays)):
            for h, v in section.items():
                name = HASH_TO_NAME.get(h, f"0x{h & 0xFFFFFFFF:08x}")
                shown = f"{len(v)} byte(s)" if label == "bytes" else v
                print(f"  {label:7} {name:28} {shown}")
        inv = inventory_of(entry)
        if inv is not None:
            version, items = inv
            print(f"  inventory version {version}, {len(items)} item(s)")
            for i in items:
                print(f"    {i.name} x{i.stack} dur={i.durability} slot={i.pos} "
                      f"q={i.quality} var={i.variant} crafter={i.crafter_name!r}")
        print(f"  flags=0x{entry.flags:04x} round-trip="
              f"{'byte-exact' if entry.encode() == args.dump else 'MISMATCH'}")
        return 0

    if not args.survey:
        ap.error("pass --survey or --dump")

    report = survey()
    print(f"{report['bodies']} .blueprint bodies, {report['data_rows']} data-bearing rows")
    print(f"  decoded and fully consumed: {report['decoded']}")
    print(f"  failed: {report['failed']}")
    for f in report["failures"]:
        print(f"    {f['body']} {f['prefab']} col13={f['value']!r}: {f['why']}")
    print(f"  byte-exact re-encode mismatches: {len(report['round_trip_mismatches'])}")
    print(f"  inventories: {report['inventory_rows']} rows across "
          f"{report['inventory_bodies']} bodies")
    print(f"  excluded: {len(report['empty_bodies'])} zero-byte bodies")
    for name, sha_list in report["name_collisions"].items():
        rows = report["collision_rows"].get(name)
        print(f"  NAME COLLISION {name}: {len(sha_list)} distinct sha256; the copy "
              f"resolved here carries {rows} data row(s). A name-keyed corpus "
              f"cannot survey both.")
    top = list(report["key_rows"].items())[:16]
    print("  top keys: " + ", ".join(f"{k}={v}" for k, v in top))
    if report["unresolved_hashes"]:
        un = list(report["unresolved_hashes"].items())[:10]
        print("  unresolved hashes: " + ", ".join(f"{k}={v}" for k, v in un))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
