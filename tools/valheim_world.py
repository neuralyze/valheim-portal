#!/usr/bin/env python3
"""Create, clone, and inspect Valheim world metadata files."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import struct
import tempfile
from pathlib import Path

DEFAULT_WORLD_VERSION = 36
DEFAULT_GENERATOR_VERSION = 2


def read_7bit(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(5):
        if offset >= len(data):
            raise ValueError("truncated string length")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid string length")


def write_7bit(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative string length")
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7f) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def read_string(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = read_7bit(data, offset)
    if length > 1024 or offset + length > len(data):
        raise ValueError("invalid metadata string")
    return data[offset:offset + length].decode("utf-8"), offset + length


def write_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return write_7bit(len(encoded)) + encoded


def stable_hash(value: str) -> int:
    hash1 = 5381
    hash2 = hash1
    encoded = value.encode("utf-16-le")
    units = [struct.unpack_from("<H", encoded, offset)[0] for offset in range(0, len(encoded), 2)]
    for index in range(0, len(units), 2):
        hash1 = (((hash1 << 5) + hash1) ^ units[index]) & 0xffffffff
        if index + 1 >= len(units):
            break
        hash2 = (((hash2 << 5) + hash2) ^ units[index + 1]) & 0xffffffff
    result = (hash1 + hash2 * 1566083941) & 0xffffffff
    return result - 0x100000000 if result >= 0x80000000 else result


def parse(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 30:
        raise ValueError("metadata file is too small")
    declared, version = struct.unpack_from("<ii", data, 0)
    if declared != len(data) - 4 or version < 20 or version > 100:
        raise ValueError("invalid metadata header")
    name, offset = read_string(data, 8)
    seed, offset = read_string(data, offset)
    if offset + 16 > len(data):
        raise ValueError("truncated metadata")
    seed_value, uid, generator = struct.unpack_from("<iqi", data, offset)
    offset += 16
    if not name or len(name) > 80 or not seed or len(seed) > 80 or generator < 1 or generator > 20:
        raise ValueError("invalid world metadata")
    return {
        "name": name,
        "seed": seed,
        "seed_value": seed_value,
        "uid": uid,
        "world_version": version,
        "generator_version": generator,
        "trailer": data[offset:],
    }


def body(metadata: dict) -> bytes:
    return b"".join([
        struct.pack("<i", metadata["world_version"]),
        write_string(metadata["name"]),
        write_string(metadata["seed"]),
        struct.pack("<iqi", metadata["seed_value"], metadata["uid"], metadata["generator_version"]),
        metadata["trailer"],
    ])


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def save(path: Path, metadata: dict) -> None:
    content = body(metadata)
    atomic_write(path, struct.pack("<i", len(content)) + content)


def versions_from_templates(root: Path | None) -> tuple[int, int]:
    best = (DEFAULT_WORLD_VERSION, DEFAULT_GENERATOR_VERSION)
    if root and root.is_dir():
        for candidate in root.glob("*/config_merged/worlds_local/*.fwl"):
            try:
                metadata = parse(candidate)
                best = max(best, (metadata["world_version"], metadata["generator_version"]))
            except (OSError, UnicodeDecodeError, ValueError, struct.error):
                continue
    return best


def valid_name(value: str) -> bool:
    return 1 <= len(value) <= 80 and all(character.isalnum() or character in "._-" for character in value)


def valid_seed(value: str) -> bool:
    return 1 <= len(value) <= 64 and all(character.isalnum() for character in value)


def refuse_existing(path: Path, force: bool) -> None:
    """Both writers os.replace onto the destination, which destroys a live
    world's seed without a trace. Nothing in this repository ever wants that
    implicitly, so require the caller to say so."""
    if path.exists() and not force:
        raise ValueError(f"{path} already exists; pass --force to overwrite it")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="print a .fwl file's metadata as JSON")
    inspect.add_argument("path", type=Path)
    generate = commands.add_parser("generate", help="write a new .fwl file for a named world and seed")
    generate.add_argument("path", type=Path)
    generate.add_argument("name")
    generate.add_argument("seed")
    generate.add_argument("--templates", type=Path)
    generate.add_argument("--force", action="store_true", help="overwrite PATH if it already exists")
    clone = commands.add_parser("clone", help="copy a .fwl file's metadata under a new world name")
    clone.add_argument("source", type=Path)
    clone.add_argument("destination", type=Path)
    clone.add_argument("name")
    clone.add_argument("--force", action="store_true", help="overwrite DESTINATION if it already exists")
    args = parser.parse_args()

    if args.command == "inspect":
        metadata = parse(args.path)
        metadata.pop("trailer")
        print(json.dumps(metadata, separators=(",", ":")))
        return 0
    if not valid_name(args.name):
        raise ValueError("invalid world name")
    if args.command == "generate":
        if not valid_seed(args.seed):
            raise ValueError("seed must contain 1 to 64 letters or digits")
        refuse_existing(args.path, args.force)
        world_version, generator_version = versions_from_templates(args.templates)
        save(args.path, {
            "name": args.name,
            "seed": args.seed,
            "seed_value": stable_hash(args.seed),
            "uid": secrets.randbelow((1 << 63) - 1) + 1,
            "world_version": world_version,
            "generator_version": generator_version,
            "trailer": b"\x00" + struct.pack("<i", 0),
        })
        print(f"wrote {args.path} name={args.name} seed={args.seed}")
        return 0
    refuse_existing(args.destination, args.force)
    metadata = parse(args.source)
    metadata["name"] = args.name
    save(args.destination, metadata)
    print(f"wrote {args.destination} name={args.name} seed={metadata['seed']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeDecodeError, ValueError, struct.error) as error:
        print(f"error: {error}", file=__import__("sys").stderr)
        raise SystemExit(2)
