#!/usr/bin/env python3
"""Extract candidate prefab/asset name tokens from a Valheim install.

Two independent evidence sources are produced, both MEASURED from the live
box rather than typed from memory:

``objectdb``
    The ``ItemStacksRewrite`` BepInEx config pair.  That mod binds one config
    entry per ``ItemDrop`` prefab present in ``ObjectDB`` at runtime, so
    ``<Prefab>_weight`` / ``<Prefab>_max_stack`` keys are an authoritative
    enumeration of every *item* prefab the running server knows about,
    including the ones added by content mods.  This is the strongest evidence
    we have for "``give <prefab>`` will resolve".

``bundle``
    Identifier-shaped tokens recovered from the game's own Unity asset
    bundles (``valheim_server_Data/StreamingAssets/SoftRef/Bundles``).  The
    bundles are UnityFS archives with LZ4/LZMA block compression; this module
    contains just enough of the container format to decompress the blocks and
    scan the resulting bytes.  Bundle evidence covers build *pieces*
    (``piece_blackforge``, ``forge_ext1``, ...) which are not items and so
    never appear in the ObjectDB dump.  A bundle hit proves the string exists
    in shipped game data -- it does not prove the string names a spawnable
    prefab, so bundle-only evidence is reported as weaker.

Usage::

    extract_prefab_names.py --valheim-root /media/big4/projects/game/valheim \\
                            --world Ulfsland \\
                            --out tools/jumpstart/data/prefab_index.json
"""

from __future__ import annotations

import argparse
import io
import json
import lzma
import os
import re
import struct
import sys
import time
from pathlib import Path

try:
    import lz4.block as lz4_block
except ImportError:  # pragma: no cover - hard dependency for bundle scanning
    lz4_block = None

TOKEN_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,63}")
STACK_KEY_RE = re.compile(r"^([A-Za-z0-9_]+)_max_stack\s*=", re.M)
WEIGHT_KEY_RE = re.compile(r"^([A-Za-z0-9_]+)_weight\s*=", re.M)


# --------------------------------------------------------------------------
# UnityFS container
# --------------------------------------------------------------------------
def _read_cstr(fh) -> str:
    out = bytearray()
    while True:
        ch = fh.read(1)
        if not ch or ch == b"\0":
            break
        out += ch
    return out.decode("utf-8", "replace")


def _decompress(data: bytes, flags: int, usize: int) -> bytes:
    kind = flags & 0x3F
    if kind == 0:
        return data
    if kind == 1:
        props = data[:5]
        filt = lzma._decode_filter_properties(lzma.FILTER_LZMA1, props)
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[filt])
        return dec.decompress(data[5:], usize)
    if kind in (2, 3):
        if lz4_block is None:
            raise RuntimeError("python-lz4 is required to read LZ4 bundles")
        return lz4_block.decompress(data, uncompressed_size=usize)
    raise ValueError(f"unsupported bundle compression {kind}")


def iter_bundle_blocks(path: Path, serialized_only: bool = True):
    """Yield decompressed data blocks of a UnityFS bundle.

    With ``serialized_only`` the walk stops once the first node (the
    serialized object file) has been covered; the trailing ``.resS`` /
    ``.resource`` nodes are texture and audio payload and contain no names.
    """
    with path.open("rb") as fh:
        if _read_cstr(fh) != "UnityFS":
            return
        version = struct.unpack(">I", fh.read(4))[0]
        _read_cstr(fh)  # unity version
        _read_cstr(fh)  # unity revision
        _size, cbis, ubis, flags = struct.unpack(">qIII", fh.read(20))
        if version >= 7:
            fh.read((-fh.tell()) % 16)
        if flags & 0x80:  # blocks info stored at the end of the file
            here = fh.tell()
            fh.seek(-cbis, os.SEEK_END)
            info = fh.read(cbis)
            fh.seek(here)
        else:
            info = fh.read(cbis)
        info = _decompress(info, flags, ubis)

        buf = io.BytesIO(info)
        buf.read(16)  # uncompressed data hash
        nblocks = struct.unpack(">i", buf.read(4))[0]
        blocks = [struct.unpack(">IIH", buf.read(10)) for _ in range(nblocks)]
        nnodes = struct.unpack(">i", buf.read(4))[0]
        nodes = []
        for _ in range(nnodes):
            offset, size, nflags = struct.unpack(">qqI", buf.read(20))
            nodes.append((offset, size, nflags, _read_cstr(buf)))

        cap = (nodes[0][0] + nodes[0][1]) if (serialized_only and nodes) else None
        fh.read((-fh.tell()) % 16)
        cursor = 0
        for usize, csize, bflags in blocks:
            raw = fh.read(csize)
            if cap is not None and cursor >= cap:
                break
            yield _decompress(raw, bflags, usize)
            cursor += usize


def read_bundle_node(path: Path, index: int = 0) -> bytes:
    """One whole node of a UnityFS bundle, decompressed.

    ``iter_bundle_blocks`` is enough to grep bytes for tokens, but a *typed*
    read needs the node reassembled with its own offsets intact, because a
    SerializedFile's object table is expressed in node-relative offsets and a
    block boundary can fall anywhere. Node 0 is the SerializedFile; the trailing
    ``.resS`` / ``.resource`` nodes are texture and audio payload.

    Same container walk as ``iter_bundle_blocks``, sharing ``_read_cstr`` and
    ``_decompress`` so there is exactly one implementation of the format.
    """
    with path.open("rb") as fh:
        if _read_cstr(fh) != "UnityFS":
            return b""
        version = struct.unpack(">I", fh.read(4))[0]
        _read_cstr(fh)  # unity version
        _read_cstr(fh)  # unity revision
        _size, cbis, ubis, flags = struct.unpack(">qIII", fh.read(20))
        if version >= 7:
            fh.read((-fh.tell()) % 16)
        if flags & 0x80:
            here = fh.tell()
            fh.seek(-cbis, os.SEEK_END)
            info = fh.read(cbis)
            fh.seek(here)
        else:
            info = fh.read(cbis)
        info = _decompress(info, flags, ubis)

        buf = io.BytesIO(info)
        buf.read(16)
        nblocks = struct.unpack(">i", buf.read(4))[0]
        blocks = [struct.unpack(">IIH", buf.read(10)) for _ in range(nblocks)]
        nnodes = struct.unpack(">i", buf.read(4))[0]
        nodes = []
        for _ in range(nnodes):
            offset, size, nflags = struct.unpack(">qqI", buf.read(20))
            nodes.append((offset, size, nflags, _read_cstr(buf)))
        if index >= len(nodes):
            return b""
        want_start = nodes[index][0]
        want_end = want_start + nodes[index][1]

        fh.read((-fh.tell()) % 16)
        out = bytearray()
        cursor = 0
        for usize, csize, bflags in blocks:
            if cursor >= want_end:
                break
            if cursor + usize <= want_start:
                fh.seek(csize, os.SEEK_CUR)
                cursor += usize
                continue
            block = _decompress(fh.read(csize), bflags, usize)
            out += block[max(0, want_start - cursor):max(0, want_end - cursor)]
            cursor += usize
        return bytes(out)



def scan_bundles(bundle_dir: Path, progress=None) -> set[str]:
    tokens: set[bytes] = set()
    files = sorted(p for p in bundle_dir.iterdir() if p.is_file())
    for index, path in enumerate(files, 1):
        try:
            for block in iter_bundle_blocks(path):
                tokens.update(m.group(0) for m in TOKEN_RE.finditer(block))
        except Exception as exc:  # a single unreadable bundle must not abort
            if progress:
                progress(f"  !! {path.name}: {exc}")
        if progress and index % 50 == 0:
            progress(f"  .. {index}/{len(files)} bundles, {len(tokens)} tokens")
    return {t.decode("ascii", "replace") for t in tokens}


# --------------------------------------------------------------------------
# ObjectDB item enumeration
# --------------------------------------------------------------------------
def scan_objectdb(config_dir: Path) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    sources: list[str] = []
    pairs = (
        ("fortis.mods.itemstacksrewrite.stacks.cfg", STACK_KEY_RE),
        ("fortis.mods.itemstacksrewrite.weights.cfg", WEIGHT_KEY_RE),
    )
    for filename, pattern in pairs:
        path = config_dir / filename
        if not path.exists():
            continue
        text = path.read_text("utf-8", "replace")
        found = set(pattern.findall(text))
        names |= found
        sources.append(f"{path} ({len(found)} entries, mtime {_mtime(path)})")
    return names, sources


def _mtime(path: Path) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))


def scan_epicloot(baseconfig_dir: Path) -> tuple[set[str], list[str]]:
    """Every quoted identifier in EpicLoot's baseconfig JSON.

    EpicLoot 1.0 data (``iteminfo.json`` in particular) enumerates real item
    prefab names per boss tier, including Deep North gear, so it is an
    independent and *current* cross-check on the stale ObjectDB snapshot.
    """
    names: set[str] = set()
    sources: list[str] = []
    if not baseconfig_dir.is_dir():
        return names, sources
    for path in sorted(baseconfig_dir.glob("*.json")):
        text = path.read_text("utf-8", "replace")
        found = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]{2,63})"', text))
        names |= found
        sources.append(f"{path.name} ({len(found)})")
    return names, [f"{baseconfig_dir}: " + ", ".join(sources)]


def scan_localization(assets: Path) -> set[str]:
    """Localization CSV keys recovered from ``resources.assets``.

    Valheim's translation table is stored uncompressed in the server's
    ``resources.assets``, one ``key,English,...`` row per line.  ``item_*`` and
    ``piece_*`` keys are derived from the prefab name, so a normalised match
    (lowercase, underscores stripped) is good evidence that a prefab of that
    name ships a display string -- which only real items and pieces do.
    """
    if not assets.exists():
        return set()
    blob = assets.read_bytes()
    keys: set[str] = set()
    for match in re.finditer(rb"(item_|piece_)([A-Za-z0-9_]{2,80}),", blob):
        keys.add((match.group(1) + match.group(2)).decode("ascii", "replace"))
    return keys


def normalise(name: str) -> str:
    return name.replace("_", "").lower()


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--valheim-root", default=os.environ.get("VALHEIM_ROOT", "/media/big4/projects/game/valheim"))
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--out", required=True, help="output prefab index JSON")
    ap.add_argument("--skip-bundles", action="store_true", help="ObjectDB evidence only (fast)")
    ap.add_argument(
        "--keep-bundle-tokens",
        action="store_true",
        help="write all ~620k bundle tokens instead of the pruned prefab-shaped subset",
    )
    args = ap.parse_args(argv)

    root = Path(args.valheim_root) / args.world
    bepinex = root / "config_merged" / "bepinex"
    server_data = root / "data" / "server" / "valheim_server_Data"
    bundle_dir = server_data / "StreamingAssets" / "SoftRef" / "Bundles"

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    objectdb, objectdb_sources = scan_objectdb(bepinex / "ItemStacksRewrite")
    log(f"objectdb items: {len(objectdb)}")
    for src in objectdb_sources:
        log(f"  <- {src}")

    epicloot, epicloot_sources = scan_epicloot(bepinex / "EpicLoot" / "baseconfig")
    log(f"epicloot identifiers: {len(epicloot)}")

    localization = scan_localization(server_data / "resources.assets")
    log(f"localization item_/piece_ keys: {len(localization)}")

    bundle: set[str] = set()
    bundle_source = None
    if not args.skip_bundles:
        if not bundle_dir.is_dir():
            log(f"!! bundle dir missing: {bundle_dir}")
        else:
            log(f"scanning bundles under {bundle_dir}")
            started = time.time()
            bundle = scan_bundles(bundle_dir, progress=log)
            bundle_source = str(bundle_dir)
            log(f"bundle tokens: {len(bundle)} in {time.time() - started:.0f}s")

    recipes = {t[len("Recipe_"):] for t in bundle if t.startswith("Recipe_")}
    log(f"recipe targets: {len(recipes)}")

    if args.keep_bundle_tokens:
        bundle_out = bundle
    else:
        # Everything a preset can legitimately reference: items (already
        # covered by the strong channels), build pieces, and station
        # extensions.  Dropping the remaining ~600k mesh/material/animation
        # tokens keeps the committed index around 100 kB instead of 4 MB.
        keep = re.compile(r"^(piece_|blackforge|forge(_ext[0-9])?$|forge_ext[0-9]|smelter$|blastfurnace$|charcoal_kiln$|windmill$|eitrrefinery$|hearth$|fire_pit$|bed$|portal(_wood)?$)")
        bundle_out = {t for t in bundle if keep.match(t)} | objectdb | epicloot | recipes
        bundle_out &= bundle
        log(f"pruned bundle tokens: {len(bundle_out)}")

    payload = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "valheim_root": str(root),
        "pruned": not args.keep_bundle_tokens,
        "sources": {
            "objectdb": objectdb_sources,
            "epicloot": epicloot_sources,
            "localization": str(server_data / "resources.assets"),
            "bundle": bundle_source,
        },
        "objectdb_items": sorted(objectdb),
        "epicloot_names": sorted(epicloot),
        "recipe_targets": sorted(recipes),
        "localization_keys": sorted(localization),
        "bundle_tokens": sorted(bundle_out),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")
    log(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
