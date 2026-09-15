#!/usr/bin/env python3
"""Read every item's WEIGHT and stack size out of Valheim's own asset bundles.

Why this exists
---------------
A jumpstart kit that exceeds the character's carry capacity leaves the player
OVERBURDENED on spawn -- which is exactly what the operator hit on
``pre-bonemass``. Refusing such a kit at build time needs a weight per prefab,
and the only honest source for that is the game's own data.

``ItemDrop.ItemData.SharedData.m_weight`` is a serialized float on a
MonoBehaviour inside the shipped UnityFS bundles. The bundles ship their
TYPETREES (MEASURED: the literal ``m_scaleWeightByQuality`` appears in 17 of the
796 bundles), so the field layout does not have to be guessed -- it is read out
of the file. This module therefore contains a small SerializedFile + blob
typetree reader on top of the bundle container walk that
``extract_prefab_names.py`` already owns; the container format has exactly one
implementation and this imports it.

Corroboration
-------------
The deployed ``ItemStacksRewrite`` config pair records one
``<Prefab>_weight`` / ``<Prefab>_max_stack`` entry per ItemDrop present in a live
``ObjectDB``, with the value the mod read from the game in the BepInEx
``Default value`` comment. That is a completely independent measurement, taken
in-process at runtime rather than out of the asset files. ``--cross-check``
compares the two and reports every disagreement. MEASURED 2026-09-15: 699
prefabs are in both sources and ZERO of them disagree on weight.


What else comes out of the same read
------------------------------------
``m_weight`` is not the only field the consumer needs, and every one of them is
on the SAME ``SharedData`` object, so reading them costs nothing extra:

* ``m_itemType`` -- which equipment slot the game puts an item in.
  ``Humanoid::EquipItem`` switches on it, so it is the ONLY honest way to tell
  that two granted items contend for one slot. Inferring a slot from a prefab
  name would be a check answering a question it is not measuring: MEASURED, the
  AdventureBackpacks prefabs ``CapeIronBackpack`` and ``BackpackSwamp`` are the
  same ``m_itemType`` 17 (Shoulder) despite the unrelated names.
* ``m_maxQuality`` -- the upper bound on a template's ``quality:``. MEASURED,
  ``ShieldBanded`` and ``ShieldIronTower`` cap at 3, not 4.
* ``m_scaleWeightByQuality`` -- the only reason quality can move weight.
  ``ItemData::GetWeight`` is ``w = m_weight * stack`` and then, only when this
  field is nonzero AND quality != 1, ``w += w * (quality - 1) * field``.

Mod-added items
---------------
A mod that adds an equippable ships the prefab in a UnityFS bundle EMBEDDED in
its own plugin DLL, so it is absent from StreamingAssets and a preset granting
it would resolve as unpriced. A second pass therefore reads the LIVE plugin
directory -- ``data/bepinex/BepInEx/plugins`` -- and pulls those bundles out by
their own declared length. Reading the live tree rather than a profile manifest
is deliberate: a manifest claiming ``scope: shared`` is not evidence that a DLL
reached the server, and this repo has already been bitten by that exact gap.

Usage::

    extract_item_weights.py --valheim-root /media/big4/projects/game/valheim \\
                            --world Ulfsland \\
                            --out tools/jumpstart/data/item_weights.json
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from extract_prefab_names import read_bundle_node  # noqa: E402  (same directory)

WEIGHT_ENTRY_RE = re.compile(
    r"# Default value: ([0-9.eE+-]+)\n# Acceptable[^\n]*\n([A-Za-z0-9_]+)_weight = ([0-9.eE+-]+)"
)
STACK_ENTRY_RE = re.compile(
    r"# Default value: (\d+)\n# Acceptable[^\n]*\n([A-Za-z0-9_]+)_max_stack = (\d+)"
)

# Unity's builtin common string buffer. A typetree node name or type whose string
# offset has bit 31 set indexes THIS table instead of the file's own string
# buffer, so without it every builtin field name ("m_Name", "float", "data")
# reads as a number. Offsets are Unity's, not ours; they are a format constant.
COMMON_STRINGS = {
    0: 'AABB', 5: 'AnimationClip', 19: 'AnimationCurve', 34: 'AnimationState',
    49: 'Array', 55: 'Base', 60: 'BitField', 69: 'bitset', 76: 'bool', 81: 'char',
    86: 'ColorRGBA', 96: 'Component', 106: 'data', 111: 'deque', 117: 'double',
    124: 'dynamic_array', 138: 'FastPropertyName', 155: 'first', 161: 'float',
    167: 'Font', 172: 'GameObject', 183: 'Generic Mono', 196: 'GradientNEW', 208: 'GUID',
    213: 'GUIStyle', 222: 'int', 226: 'list', 231: 'long long', 241: 'map',
    245: 'Matrix4x4f', 256: 'MdFour', 263: 'MonoBehaviour', 277: 'MonoScript',
    288: 'm_ByteSize', 299: 'm_Curve', 307: 'm_EditorClassIdentifier',
    331: 'm_EditorHideFlags', 349: 'm_Enabled', 359: 'm_ExtensionPtr',
    374: 'm_GameObject', 387: 'm_Index', 395: 'm_IsArray', 405: 'm_IsStatic',
    416: 'm_MetaFlag', 427: 'm_Name', 434: 'm_ObjectHideFlags', 452: 'm_PrefabInternal',
    469: 'm_PrefabParentObject', 490: 'm_Script', 499: 'm_StaticEditorFlags',
    519: 'm_Type', 526: 'm_Version', 536: 'Object', 543: 'pair', 548: 'PPtr<Component>',
    564: 'PPtr<GameObject>', 581: 'PPtr<Material>', 596: 'PPtr<MonoBehaviour>',
    616: 'PPtr<MonoScript>', 633: 'PPtr<Object>', 646: 'PPtr<Prefab>',
    659: 'PPtr<Sprite>', 672: 'PPtr<TextAsset>', 688: 'PPtr<Texture>',
    702: 'PPtr<Texture2D>', 718: 'PPtr<Transform>', 734: 'Prefab', 741: 'Quaternionf',
    753: 'Rectf', 759: 'RectInt', 767: 'RectOffset', 778: 'second', 785: 'set',
    789: 'short', 795: 'size', 800: 'SInt16', 807: 'SInt32', 814: 'SInt64', 821: 'SInt8',
    827: 'staticvector', 840: 'string', 847: 'TextAsset', 857: 'TextMesh',
    866: 'Texture', 874: 'Texture2D', 884: 'Transform', 894: 'TypelessData',
    907: 'UInt16', 914: 'UInt32', 921: 'UInt64', 928: 'UInt8', 934: 'unsigned int',
    947: 'unsigned long long', 966: 'unsigned short', 981: 'vector', 988: 'Vector2f',
    997: 'Vector3f', 1006: 'Vector4f', 1015: 'm_ScriptingClassIdentifier',
    1042: 'Gradient', 1051: 'Type*', 1057: 'int2_storage', 1070: 'int3_storage',
    1083: 'BoundsInt', 1093: 'm_CorrespondingSourceObject', 1121: 'm_PrefabInstance',
    1138: 'm_PrefabAsset', 1152: 'FileSize', 1161: 'Hash128', 1169: 'RenderingLayerMask',
    1188: 'fixed_array', 1200: 'EntityId', 1209: 'LoadableObjectId',
    1226: 'LoadableSceneId',
}

CLASS_GAMEOBJECT = 1
CLASS_MONOBEHAVIOUR = 114

# Typetree meta flag bit 0x4000 = "align this field to 4 bytes after reading".
ALIGN_FLAG = 0x4000
# Typetree type flag bit 0x1 = "this node is an array".
ARRAY_FLAG = 0x1

# ItemDrop.ItemData.ItemType, MEASURED from the enum in assembly_valheim.dll
# (Valheim 1.0.12, monodis). The numbers are what m_itemType actually holds in
# the bundles; the names are only for the reader. 8 is the one value absent from
# the enum in this build, so a prefab reporting it stays unnamed rather than
# being guessed at.
ITEM_TYPES = {
    0: "None", 1: "Material", 2: "Consumable", 3: "OneHandedWeapon", 4: "Bow",
    5: "Shield", 6: "Helmet", 7: "Chest", 9: "Ammo", 10: "Customization",
    11: "Legs", 12: "Hands", 13: "Trophy", 14: "TwoHandedWeapon", 15: "Torch",
    16: "Misc", 17: "Shoulder", 18: "Utility", 19: "Tool",
    20: "Attach_Atgeir", 21: "Fish", 22: "TwoHandedWeaponLeft",
    23: "AmmoNonEquipable", 24: "Trinket",
}


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
class Reader:
    """Little-endian cursor over a SerializedFile."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0):
        self.buf = buf
        self.pos = pos

    def take(self, n: int) -> bytes:
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def unpack(self, fmt: str, n: int):
        value = struct.unpack_from(fmt, self.buf, self.pos)
        self.pos += n
        return value[0]

    def u8(self) -> int:
        return self.unpack("<B", 1)

    def i32(self) -> int:
        return self.unpack("<i", 4)

    def u32(self) -> int:
        return self.unpack("<I", 4)

    def i64(self) -> int:
        return self.unpack("<q", 8)

    def cstr(self) -> str:
        end = self.buf.index(b"\0", self.pos)
        out = self.buf[self.pos:end].decode("utf-8", "replace")
        self.pos = end + 1
        return out

    def align4(self) -> None:
        self.pos = (self.pos + 3) & ~3


class Node:
    __slots__ = ("type", "name", "size", "flags", "meta", "children")

    def __init__(self, type_: str, name: str, size: int, flags: int, meta: int):
        self.type = type_
        self.name = name
        self.size = size
        self.flags = flags
        self.meta = meta
        self.children: list[Node] = []


PRIMITIVES = {
    "bool": ("<?", 1), "char": ("<B", 1), "SInt8": ("<b", 1), "UInt8": ("<B", 1),
    "SInt16": ("<h", 2), "short": ("<h", 2), "UInt16": ("<H", 2),
    "unsigned short": ("<H", 2),
    "SInt32": ("<i", 4), "int": ("<i", 4), "UInt32": ("<I", 4),
    "unsigned int": ("<I", 4), "Type*": ("<I", 4),
    "SInt64": ("<q", 8), "long long": ("<q", 8), "UInt64": ("<Q", 8),
    "unsigned long long": ("<Q", 8), "FileSize": ("<Q", 8),
    "float": ("<f", 4), "double": ("<d", 8),
}


def read_value(reader: Reader, node: Node):
    """Read one typetree node, in Unity's own serializer order.

    Three rules carry the whole format and getting any of them wrong
    desynchronises the cursor for the rest of the object:
      * a primitive reads its width, then aligns to 4 if its meta flag says so;
      * a container is a node whose FIRST CHILD is typed ``Array`` -- the array
        marker is the child's TYPE NAME, not a flag on the node -- and it reads
        an int count followed by that many elements, aligning if either the node
        or the ``Array`` child asks for it;
      * anything else is a struct: its children in declaration order.
    """
    align = bool(node.meta & ALIGN_FLAG)
    kind = node.type

    prim = PRIMITIVES.get(kind)
    if prim is not None:
        value = reader.unpack(*prim)
    elif kind == "string":
        value = reader.take(reader.i32()).decode("utf-8", "replace")
        align = True  # a serialized string is always followed by padding
    elif kind == "TypelessData":
        value = reader.take(reader.i32())
        align = True
    elif kind == "pair" and len(node.children) == 2:
        value = (read_value(reader, node.children[0]), read_value(reader, node.children[1]))
    elif node.children and node.children[0].type == "Array":
        array = node.children[0]
        align = align or bool(array.meta & ALIGN_FLAG)
        count = reader.i32()
        if count < 0:
            raise ValueError(f"negative array length {count} for {node.name!r}")
        element = array.children[1]
        prim = PRIMITIVES.get(element.type)
        if prim is not None and not element.children:
            fmt, width = prim
            value = list(struct.unpack_from(f"<{count}{fmt[1]}", reader.take(count * width)))
            if element.meta & ALIGN_FLAG:
                reader.align4()
        else:
            value = [read_value(reader, element) for _ in range(count)]
    else:
        value = {}
        for child in node.children:
            value[child.name] = read_value(reader, child)

    if align:
        reader.align4()
    return value


# --------------------------------------------------------------------------
# SerializedFile
# --------------------------------------------------------------------------
def _read_typetree_blob(reader: Reader, version: int) -> Node:
    count = reader.i32()
    string_size = reader.i32()
    stride = 32 if version >= 19 else 24
    raw = reader.take(count * stride)
    strings = reader.take(string_size)

    def name_at(offset: int) -> str:
        if offset & 0x80000000:
            return COMMON_STRINGS.get(offset & 0x7FFFFFFF, str(offset & 0x7FFFFFFF))
        end = strings.index(b"\0", offset)
        return strings[offset:end].decode("utf-8", "replace")

    root: Node | None = None
    stack: list[tuple[int, Node]] = []
    for index in range(count):
        base = index * stride
        version_, level, flags, type_off, name_off, size, _idx, meta = struct.unpack_from(
            "<HBBIIiiI", raw, base
        )
        del version_
        node = Node(name_at(type_off), name_at(name_off), size, flags, meta)
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            root = node
        stack.append((level, node))
    if root is None:
        raise ValueError("empty typetree")
    return root


def parse_serialized(buf: bytes):
    """(objects, types) of a SerializedFile with typetrees.

    ``objects`` is a list of ``(path_id, class_id, start, size, type_index)``.
    """
    reader = Reader(buf)
    reader.unpack(">I", 4)  # legacy metadata size
    reader.unpack(">I", 4)  # legacy file size
    version = reader.unpack(">I", 4)
    reader.unpack(">I", 4)  # legacy data offset
    if version < 22:
        raise ValueError(f"unsupported SerializedFile version {version}")
    big_endian = bool(reader.u8())
    if big_endian:
        raise ValueError("big-endian SerializedFile; Valheim ships little-endian")
    reader.take(3)  # reserved
    # The endianness byte only takes effect from the UNITY VERSION STRING onward:
    # these three are still BIG-endian. Reading them little-endian yields a
    # plausible-looking but astronomically wrong data_offset, which then makes
    # every object read garbage -- measured, and the reason this is spelled out.
    reader.unpack(">I", 4)  # metadata size
    reader.unpack(">q", 8)  # file size
    data_offset = reader.unpack(">q", 8)
    reader.unpack(">q", 8)  # unknown

    reader.cstr()  # unity version
    reader.unpack("<i", 4)  # target platform
    has_typetree = bool(reader.u8())
    if not has_typetree:
        raise ValueError("bundle ships no typetrees")

    type_count = reader.i32()
    types = []
    for _ in range(type_count):
        class_id = reader.i32()
        reader.u8()  # is stripped
        reader.unpack("<h", 2)  # script type index
        if class_id == CLASS_MONOBEHAVIOUR:
            reader.take(16)  # script id hash
        reader.take(16)  # old type hash
        types.append((class_id, _read_typetree_blob(reader, version)))
        # version >= 21 writes a dependency int array after each typetree. Skipping
        # it desynchronises the cursor and every later type reads as garbage, which
        # is exactly the shape of a check that answers a question it is not
        # measuring -- so it is consumed explicitly.
        reader.take(4 * reader.i32())

    object_count = reader.i32()
    objects = []
    for _ in range(object_count):
        reader.align4()
        path_id = reader.i64()
        start = reader.i64()
        size = reader.u32()
        type_index = reader.i32()
        objects.append((path_id, types[type_index][0], data_offset + start, size, type_index))
    return objects, types


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
def scan_bundle(path: Path) -> tuple[dict[str, dict], dict[str, float]]:
    """(prefab -> item facts, status-effect token -> carry weight bonus)."""
    buf = read_bundle_node(path, 0)
    if not buf:
        return {}, {}
    objects, types = parse_serialized(buf)

    gameobject_names: dict[int, str] = {}
    pending: list[tuple[int, dict]] = []
    carry: dict[str, float] = {}

    for path_id, class_id, start, size, type_index in objects:
        root = types[type_index][1]
        fields = {child.name for child in root.children}
        if class_id == CLASS_GAMEOBJECT:
            if "m_Name" not in fields:
                continue
            tree = read_value(Reader(buf, start), root)
            gameobject_names[path_id] = tree.get("m_Name")
            continue
        if class_id != CLASS_MONOBEHAVIOUR:
            continue
        if "m_itemData" in fields:
            tree = read_value(Reader(buf, start), root)
            shared = (tree.get("m_itemData") or {}).get("m_shared") or {}
            if "m_weight" not in shared:
                continue
            pending.append((
                (tree.get("m_GameObject") or {}).get("m_PathID"),
                {
                    "weight": round(float(shared["m_weight"]), 6),
                    "max_stack": int(shared.get("m_maxStackSize") or 1),
                    "scale_weight_by_quality": round(
                        float(shared.get("m_scaleWeightByQuality") or 0.0), 6
                    ),
                    # m_itemType decides which equipment slot the game puts this
                    # in (Humanoid::EquipItem switches on it) and m_maxQuality
                    # bounds a template's `quality:`. Both are plain int32s on
                    # SharedData, right next to the weight, so reading them costs
                    # nothing and stops the consumer inferring a slot from a name.
                    "item_type": int(shared.get("m_itemType") or 0),
                    "max_quality": int(shared.get("m_maxQuality") or 1),
                    "token": shared.get("m_name"),
                },
            ))
        elif "m_addMaxCarryWeight" in fields:
            tree = read_value(Reader(buf, start), root)
            bonus = float(tree.get("m_addMaxCarryWeight") or 0.0)
            if bonus:
                carry[str(tree.get("m_name") or path_id)] = round(bonus, 6)
        del size
    items = {}
    for gameobject_id, facts in pending:
        name = gameobject_names.get(gameobject_id)
        if name:
            items[name] = facts
    return items, carry


UNITYFS_MAGIC = b"UnityFS\x00"


def embedded_bundles(dll: Path) -> list[bytes]:
    """Every UnityFS bundle embedded as a managed resource in one plugin DLL.

    A mod that adds an equippable ships its prefab inside itself, so the game's
    StreamingAssets directory does not price it and a preset granting it would
    resolve as UNPRICED and refuse the whole template. The bundles are byte-for-
    byte the same container the shipped ones use -- MEASURED on
    AdventureBackpacks.dll: 8 bundles, all UnityFS version 8, unity 6000.0.58f2,
    flags 0x243 -- so the reader that already exists reads them unchanged.

    The slice length comes from the bundle's OWN declared total size (the
    big-endian int64 after the two version strings), not from the distance to the
    next magic. Guessing the length from the next match would silently hand the
    reader a concatenation of every later bundle, and bit 0x80 of the flags means
    "block info lives at the END of the bundle" -- which for an over-long slice
    is the end of something else entirely.
    """
    buf = dll.read_bytes()
    out: list[bytes] = []
    at = buf.find(UNITYFS_MAGIC)
    while at >= 0:
        cursor = at + len(UNITYFS_MAGIC) + 4  # magic + big-endian format version
        for _ in range(2):  # unity version, unity revision
            end = buf.find(b"\x00", cursor)
            if end < 0:
                cursor = -1
                break
            cursor = end + 1
        if cursor > 0 and cursor + 8 <= len(buf):
            (size,) = struct.unpack_from(">q", buf, cursor)
            if 0 < size <= len(buf) - at:
                out.append(buf[at:at + size])
                at = buf.find(UNITYFS_MAGIC, at + size)
                continue
        at = buf.find(UNITYFS_MAGIC, at + 1)
    return out


def scan_mod_plugins(plugin_dir: Path, tmp: Path) -> tuple[dict[str, dict], dict[str, float], list[str]]:
    """(prefab -> facts, carry bonuses, one note per DLL that yielded items).

    Reads the LIVE plugin directory, deliberately: a manifest claiming
    `scope: shared` is not evidence that a DLL reached the server, and this tree
    has already been bitten by exactly that (CarrySkill.dll is `shared` in the
    manifest and present only in the client cache). If a mod's items are priced
    here, the mod is on the server.
    """
    items: dict[str, dict] = {}
    carry: dict[str, float] = {}
    notes: list[str] = []
    if not plugin_dir.is_dir():
        return items, carry, notes
    for dll in sorted(plugin_dir.rglob("*.dll")):
        found_here: dict[str, dict] = {}
        for index, blob in enumerate(embedded_bundles(dll)):
            staged = tmp / f"{dll.stem}.{index}.bundle"
            staged.write_bytes(blob)
            try:
                found, bonuses = scan_bundle(staged)
            except Exception:
                # A mod bundle that does not parse is not a reason to lose the
                # 2,420 vanilla weights. It is reported by its absence: any
                # prefab a preset names and this pass missed comes out UNPRICED.
                continue
            finally:
                staged.unlink(missing_ok=True)
            found_here.update(found)
            carry.update(bonuses)
        for name, facts in found_here.items():
            facts["bundle"] = f"{dll.parent.name}/{dll.name}"
            items[name] = facts
        if found_here:
            notes.append(
                f"{dll.parent.name}/{dll.name}: {len(found_here)} item(s) "
                f"({', '.join(sorted(found_here))})"
            )
    return items, carry, notes


# The mod's own English translation table, shipped as an uncompressed managed
# resource inside the DLL. It is the ONLY link between a config section (keyed by
# an item's DISPLAY name, "[Backpack: Bloodbag Wetpack]") and a prefab (keyed by
# its localisation token, "$vapok_mod_item_backpack_swamp"). Matching the two by
# eye would be a guess; matching them through the mod's own table is a read.
TRANSLATION_RE = re.compile(rb'"([a-z0-9_]+)":\s*"([^"\\]{1,120})"')
CFG_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
CFG_ENTRY_RE = re.compile(r"^(?P<key>[^#=\[\]][^=]*?)\s*=\s*(?P<value>.*)$")
GRID_RE = re.compile(r'"x"\s*:\s*([0-9.]+)\s*,\s*"y"\s*:\s*([0-9.]+)')


def read_translations(dll: Path) -> dict[str, str]:
    """token (without the leading $) -> English display name, from the DLL."""
    blob = dll.read_bytes()
    out: dict[str, str] = {}
    for token, value in TRANSLATION_RE.findall(blob):
        try:
            out.setdefault(token.decode("ascii"), value.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return out


def read_cfg_sections(path: Path) -> dict[str, dict[str, str]]:
    """BepInEx cfg as {section: {key: active value}}, comments discarded."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in path.read_text("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = CFG_SECTION_RE.match(stripped)
        if head:
            current = sections.setdefault(head.group("name"), {})
            continue
        if current is None:
            continue
        entry = CFG_ENTRY_RE.match(stripped)
        if entry:
            current[entry.group("key").strip()] = entry.group("value").strip()
    return sections


def scan_containers(plugin_dir: Path, cfg_dir: Path, items: dict[str, dict]) -> dict[str, dict]:
    """AdventureBackpacks' per-backpack grid, weight multiplier and carry bonus.

    A backpack is the only granted item whose own inventory can hold other
    granted items, so a consumer that wants to put things IN one needs the grid
    it provides -- and that grid is configuration, not asset data, so it cannot
    come out of the bundles with everything else.

    Three measured facts are stitched together, and none of them is a guess:
      1. The prefabs and their localisation tokens, already read out of the
         bundles embedded in AdventureBackpacks.dll.
      2. The mod's own English translation table, read out of the same DLL, which
         turns a token into the display name BepInEx uses as a section heading.
      3. The LIVE config. BepInEx's config directory in the running server is a
         symlink to the deployed config tree, so this reads the file the process
         actually reads.

    Caveats recorded in the output rather than assumed away:
      * `weight_multiplier` and `carry_bonus` are CLIENT-SIDE. MEASURED: the mod
        annotates every key "[Synced with Server]" and ships a
        Vapok.Common.Managers.Configuration.ConfigSyncBase, but the only RPCs in
        the assembly are PMAdminStatusSync / RPC_InitialAdminSync /
        RPC_AdminPieceAddRemove -- PieceManager admin-list syncing. There is NO
        config-value RPC, so every client reads its own cfg and the annotation
        has no transport behind it. Do not treat these numbers as enforced.
      * `grid` is per QUALITY LEVEL, from the same client-local config. The
        smallest level is therefore the only size every client can be assumed to
        provide, which is what `min_slots` records.
    """
    containers: dict[str, dict] = {}
    for dll in sorted(plugin_dir.rglob("*.dll")):
        cfg = cfg_dir / f"vapok.mods.{dll.stem.lower()}.cfg"
        if not cfg.exists():
            continue
        display = read_translations(dll)
        sections = read_cfg_sections(cfg)
        by_display = {
            display[facts["token"].lstrip("$")]: (name, facts)
            for name, facts in items.items()
            if facts.get("token", "").lstrip("$") in display
        }
        for section, entries in sections.items():
            head, _, label = section.partition(": ")
            if head != "Backpack" or label not in by_display:
                continue
            prefab, _facts = by_display[label]
            grid: dict[int, list[int]] = {}
            for key, value in entries.items():
                if not key.startswith("Backpack Size - Level "):
                    continue
                size = GRID_RE.search(value)
                if size:
                    grid[int(key.rsplit(" ", 1)[1])] = [
                        int(float(size.group(1))),
                        int(float(size.group(2))),
                    ]
            if not grid:
                continue
            slots = {level: w * h for level, (w, h) in sorted(grid.items())}
            containers[prefab] = {
                "mod": f"{dll.parent.name}/{dll.name}",
                "cfg": str(cfg),
                "cfg_section": section,
                "display_name": label,
                "biome": entries.get("Backpack Biome"),
                "grid": {str(level): grid[level] for level in sorted(grid)},
                "slots": {str(level): slots[level] for level in sorted(slots)},
                "min_slots": min(slots.values()),
                "weight_multiplier": float(entries.get("Weight Multiplier", 1.0)),
                "carry_bonus": int(float(entries.get("Carry Bonus", 0))),
                "speed_modifier": float(entries.get("Speed Modifier", 0.0)),
                "enforced": False,
            }
    return containers


def read_isr_cfg(path: Path, pattern: re.Pattern) -> tuple[dict[str, float], list[str]]:
    """(prefab -> value from the BepInEx `Default value` comment, overrides).

    The mod binds one entry per live ObjectDB ItemDrop and BepInEx writes the
    value it read from the game into the comment. Where the ACTIVE value differs
    from that default the mod is rewriting the game, so the difference is
    reported rather than silently averaged away.
    """
    if not path.exists():
        return {}, []
    text = path.read_text("utf-8", errors="replace")
    defaults: dict[str, float] = {}
    overrides: list[str] = []
    for default, prefab, active in pattern.findall(text):
        defaults[prefab] = float(default)
        if float(active) != float(default):
            overrides.append(f"{prefab}: game {default} -> config {active}")
    return defaults, overrides


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--valheim-root", default="/media/big4/projects/game/valheim")
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--out", type=Path, default=HERE / "data" / "item_weights.json")
    ap.add_argument(
        "--cross-check",
        action="store_true",
        help="compare bundle weights against the ItemStacksRewrite ObjectDB dump",
    )
    args = ap.parse_args(argv)

    root = Path(args.valheim_root) / args.world
    bundles = root / "data/bepinex/valheim_server_Data/StreamingAssets/SoftRef/Bundles"
    if not bundles.is_dir():
        ap.error(f"no bundle directory at {bundles}")

    started = time.time()
    items: dict[str, dict] = {}
    carry: dict[str, float] = {}
    scanned = 0
    failures: list[str] = []
    for bundle in sorted(p for p in bundles.iterdir() if p.is_file()):
        scanned += 1
        try:
            found, bonuses = scan_bundle(bundle)
        except Exception as exc:  # one unreadable bundle must not abort the scan
            failures.append(f"{bundle.name}: {exc}")
            continue
        for name, facts in found.items():
            facts["bundle"] = bundle.name
            items[name] = facts
        carry.update(bonuses)
        print(f"  .. {scanned} bundles, {len(items)} items", end="\r", file=sys.stderr)
    print(file=sys.stderr)

    # Mod-added items live inside their own plugin DLLs, so a second pass over
    # the LIVE plugin tree.
    #
    # This pass is ADDITIVE ONLY, and that is the whole point. MEASURED: 113 of
    # the prefabs in the mods' embedded bundles carry VANILLA names -- Wood,
    # Stone, Iron, BeltStrength, Ooze -- because a mod that adds a recipe or a
    # build piece ships a copy of every ingredient prefab it references. Those
    # copies are the mod author's snapshot, not the ObjectDB the server runs, and
    # letting them win would silently overwrite 2,420 weights that were
    # cross-validated against a live ObjectDB dump with zero disagreements. So a
    # collision NEVER overrides; it is recorded, with the numbers, in
    # `mod_plugin_collisions`, which lists only the ones whose weight actually
    # DIFFERS -- a real mod rewrite of a vanilla item shows up there and is then
    # a decision to take, not a default to inherit.
    plugin_dir = root / "data/bepinex/BepInEx/plugins"
    with tempfile.TemporaryDirectory(prefix="jumpstart-modbundles-") as staging:
        mod_found, mod_carry, mod_notes = scan_mod_plugins(plugin_dir, Path(staging))
    mod_items = {name: facts for name, facts in mod_found.items() if name not in items}
    mod_collisions = [
        {
            "prefab": name,
            "vanilla_weight": items[name]["weight"],
            "mod_weight": facts["weight"],
            "mod_bundle": facts["bundle"],
        }
        for name, facts in sorted(mod_found.items())
        if name in items and abs(items[name]["weight"] - facts["weight"]) > 1e-4
    ]
    mod_shadowed = sorted(set(mod_found) & set(items))
    items.update(mod_items)
    # Carry-weight status effects are keyed by localisation token, so a mod's own
    # SE_Stats cannot collide with a vanilla one by accident. Additive is right
    # here: a mod that adds a carry effect is a real effect on this server.
    for token, bonus in mod_carry.items():
        carry.setdefault(token, bonus)

    # Container grids come from the LIVE BepInEx config directory, which in the
    # running server is a symlink into the deployed config tree -- so this is the
    # file the process reads, not the staging copy.
    bepinex_cfg = root / "config_merged/bepinex"
    containers = scan_containers(plugin_dir, bepinex_cfg, mod_found)

    cfg_dir = root / "config_merged/bepinex/ItemStacksRewrite"
    cfg_weights, weight_overrides = read_isr_cfg(
        cfg_dir / "fortis.mods.itemstacksrewrite.weights.cfg", WEIGHT_ENTRY_RE
    )
    cfg_stacks, stack_overrides = read_isr_cfg(
        cfg_dir / "fortis.mods.itemstacksrewrite.stacks.cfg", STACK_ENTRY_RE
    )

    shared = sorted(set(cfg_weights) & set(items))
    disagree = [
        {"prefab": p, "objectdb": cfg_weights[p], "bundle": items[p]["weight"]}
        for p in shared
        if abs(cfg_weights[p] - items[p]["weight"]) > 1e-4
    ]

    weights = {name: facts["weight"] for name, facts in items.items()}
    evidence = {
        name: ("mod-bundle" if name in mod_items else "bundle") for name in items
    }
    for prefab, value in cfg_weights.items():
        if prefab not in weights:
            weights[prefab] = value
            evidence[prefab] = "objectdb"

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generated_by": "tools/jumpstart/extract_item_weights.py",
        "valheim_root": str(root),
        "bundles_scanned": scanned,
        "bundle_failures": failures,
        "sources": {
            "bundle": f"{bundles} ({sum(1 for e in evidence.values() if e == 'bundle')} items,"
            " ItemDrop.m_shared.m_weight read through the bundle's own typetree)",
            "objectdb": f"{cfg_dir} ({len(cfg_weights)} weight entries,"
            f" {len(cfg_stacks)} stack entries, BepInEx 'Default value' comments)",
            "mod_plugins": f"{plugin_dir} ({len(mod_items)} items from UnityFS bundles"
            " embedded in live plugin DLLs)",
        },
        "mod_plugin_items": mod_notes,
        "mod_plugin_collisions": mod_collisions,
        "mod_plugin_shadowed": mod_shadowed,
        # Items whose OWN inventory can hold other granted items, with the grid
        # they provide per quality level. Read from the live BepInEx config, not
        # from asset data, because a grid is configuration. `enforced: false` is
        # part of the measurement: see scan_containers for why the mod's
        # "[Synced with Server]" annotation has no transport behind it.
        "containers": containers,
        "cross_check": {
            "shared_prefabs": len(shared),
            "disagreements": disagree,
            "objectdb_weight_overrides": weight_overrides,
            "objectdb_stack_overrides": stack_overrides,
        },
        "carry_weight_status_effects": carry,
        "evidence": evidence,
        "weights": weights,
        "max_stack": {name: facts["max_stack"] for name, facts in items.items()},
        # m_itemType and m_maxQuality, for every prefab read from a bundle. The
        # ObjectDB cfg carries neither, so an objectdb-only prefab is absent from
        # both maps and the consumer must treat "absent" as "unknown", never as a
        # default -- a defaulted slot silently makes two items stop contending.
        "item_type": {name: facts["item_type"] for name, facts in items.items()},
        "item_type_names": {
            str(value): name for value, name in sorted(ITEM_TYPES.items())
        },
        "max_quality": {name: facts["max_quality"] for name, facts in items.items()},
        "scale_weight_by_quality": {
            name: facts["scale_weight_by_quality"]
            for name, facts in items.items()
            if facts["scale_weight_by_quality"]
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n", "utf-8")

    print(
        f"{len(weights)} weights ({sum(1 for e in evidence.values() if e == 'bundle')} bundle,"
        f" {len(mod_items)} mod-bundle,"
        f" {sum(1 for e in evidence.values() if e == 'objectdb')} objectdb-only),"
        f" {len(shared)} cross-checked, {len(disagree)} disagreements,"
        f" {len(carry)} carry-weight status effects,"
        f" {time.time() - started:.1f}s -> {args.out}"
    )
    if args.cross_check:
        for row in disagree:
            print(f"  !! {row['prefab']}: objectdb {row['objectdb']} vs bundle {row['bundle']}")
    for note in mod_notes:
        print(f"  ++ {note}")
    print(
        f"  ++ {len(mod_shadowed)} vanilla prefab name(s) also appear in mod bundles"
        f" and were NOT overridden; {len(mod_collisions)} of them disagree on weight"
    )
    for row in mod_collisions:
        print(
            f"  !! {row['prefab']}: vanilla {row['vanilla_weight']} vs"
            f" {row['mod_bundle']} {row['mod_weight']} -- vanilla kept"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
