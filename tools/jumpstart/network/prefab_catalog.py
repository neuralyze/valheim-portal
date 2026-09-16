#!/usr/bin/env python3
"""Name the prefab hashes the map cannot resolve, by reading the MOD BUNDLES.

THE DEFECT
==========
MEASURED on the archived pre-wipe Ulfsland, catalogued against
`assembly_valheim.dll` alone: 90,740 of 91,734 objects carried a prefab hash the
catalog could not name -- and those 90,740 objects are only 563 DISTINCT hashes.
An unnamed hash is invisible twice over: `worldintel.category("")` returns
"unknown" and `retain()` drops it, so the whole 9,290-piece installation tier
reached the browser map as nothing at all. The world was built and the map was
empty.

The reason is not subtle once stated. `CatalogFromFiles` scans raw bytes out of
`.dll`, `.assets`, `.json`, `.cfg`, `.yml`, `.yaml`, `.txt` and extensionless
SoftRef manifests. Our bodies are built from MOD pieces, and a mod ships its
prefabs inside UnityFS asset bundles whose block data is LZ4 or LZMA compressed.
A byte scan of a compressed bundle finds nothing, because there is nothing there
to find until it is decompressed.

WHY THIS IS A NAME DUMP AND NOT A PREFAB LIST
=============================================
`extract_prefab_names.py` is explicit that bundle evidence is WEAK: an
identifier-shaped token inside a bundle proves the string exists in shipped data,
not that it names a spawnable prefab. That weakness does not matter here, and the
reason is the whole design: the FILTER is the hash. A token only enters the
catalog if `StableHash(token)` equals a hash some ZDO in the world actually
carries, and a 32-bit hash collision against a specific unresolved hash is the
proof. So a deliberately over-broad token dump is exactly the right input.

ONE BUNDLE READER
=================
`extract_prefab_names.read_bundle_node` and `iter_bundle_blocks` are the single
implementation of the UnityFS container in this repo, deliberately made public so
the format has one reader. This module imports them. It does NOT contain a second
copy, and if the container format is ever wrong it is wrong in one place.

OUTPUT
======
A plain `.txt` file, one candidate name per line. That extension is already in
`CatalogFromFiles`'s accepted list and `tokenRE` already lifts one identifier per
line, so the manifest is consumed by the EXISTING Go catalog path with no new
format reader on the Go side either. Pass it alongside the assembly:

    world-analysis -catalog <assembly.dll> -catalog-extra <manifest.txt>

or put it in a directory the catalog already walks.

Usage::

    prefab_catalog.py build --valheim-root /media/big4/projects/game/valheim \\
                            --world Ulfsland \\
                            --out tools/jumpstart/network/data/Ulfsland/prefab_names.txt
    prefab_catalog.py resolve --snapshot snap.json --names prefab_names.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import DATA, JUMPSTART

sys.path.insert(0, str(JUMPSTART))
sys.path.insert(0, str(JUMPSTART / "blueprints"))
from extract_prefab_names import TOKEN_RE, iter_bundle_blocks  # noqa: E402

# `StringExtensionMethods::GetStableHashCode`, from the ONE implementation in this
# repo that is validated against real data.
#
# It is imported and not reimplemented, and that is not a style preference: the
# first cut of this module wrote its own one-line DJB2 and it was WRONG, because
# Valheim's hash is an INTERLEAVED DOUBLE DJB2 -- even-indexed characters into one
# accumulator, odd into another, combined as `a + b * 1566083941`. The wrong hash
# did not throw and did not look broken: it produced a clean, confident report
# saying zero of 563 unresolved prefabs were nameable, which is exactly the shape of
# defect this project has been paying for all night. `data_entry.stable_hash`
# reproduces all 13 resolved prefab hashes in this world's own snapshot exactly.
from data_entry import stable_hash  # noqa: E402


# Where a deployed Valheim tree keeps things full of prefab names. Every one of
# these is a DIRECTORY that is walked; a missing one is skipped and reported rather
# than being a failure, because a world with no mods legitimately has no plugins.
BUNDLE_ROOTS = (
    "data/bepinex/BepInEx/plugins",
    "data/bepinex/valheim_server_Data/StreamingAssets/SoftRef/Bundles",
)

# UnityFS's own magic. A bundle is identified by its CONTENT and never by its name:
# More World Locations AIO ships 267 bundles with no extension at all
# (`Bundles/mwl_foresthouse2`), and an extension allowlist would have missed every
# one of them -- which is the same mistake as matching only the two vanilla SoftRef
# manifest names, already paid for once in this repo.
UNITYFS_MAGIC = b"UnityFS"

# A bundle smaller than this holds no prefab worth naming and a bundle larger than
# this is not one; both bounds exist so a pathological file cannot stall the walk.
MIN_BUNDLE_BYTES = 64
MAX_BUNDLE_BYTES = 512 << 20


def is_bundle(path: Path) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if not MIN_BUNDLE_BYTES <= size <= MAX_BUNDLE_BYTES:
        return False
    try:
        with path.open("rb") as fh:
            return fh.read(len(UNITYFS_MAGIC)) == UNITYFS_MAGIC
    except OSError:
        return False


def find_bundles(world_dir: Path) -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    notes: list[str] = []
    for rel in BUNDLE_ROOTS:
        root = world_dir / rel
        if not root.is_dir():
            notes.append(f"{rel}: absent")
            continue
        here = sorted(p for p in root.rglob("*") if p.is_file() and is_bundle(p))
        found.extend(here)
        notes.append(f"{rel}: {len(here)} UnityFS bundle(s)")
    return found, notes


def scan(bundles: list[Path], progress=None) -> tuple[set[str], list[str]]:
    """Identifier-shaped tokens out of every bundle, plus the ones that failed.

    A single unreadable bundle is reported and skipped, never fatal: a mod shipping
    one bundle in a container version this reader does not know must not cost the
    other 1,062 bundles their names.
    """
    tokens: set[bytes] = set()
    failures: list[str] = []
    for index, path in enumerate(bundles, 1):
        try:
            for block in iter_bundle_blocks(path):
                tokens.update(m.group(0) for m in TOKEN_RE.finditer(block))
        except Exception as exc:
            failures.append(f"{path}: {exc}")
        if progress and index % 100 == 0:
            progress(f"  .. {index}/{len(bundles)} bundles, {len(tokens)} tokens")
    return {t.decode("ascii", "replace") for t in tokens}, failures


def names_path(world: str) -> Path:
    return DATA / world / "prefab_names.txt"


def build(valheim_root: Path, world: str, out: Path | None = None,
          progress=None) -> dict:
    world_dir = valheim_root / world
    if not world_dir.is_dir():
        raise SystemExit(f"no world tree at {world_dir}")
    bundles, notes = find_bundles(world_dir)
    for note in notes:
        if progress:
            progress(f"  {note}")
    if not bundles:
        raise SystemExit(
            f"found no UnityFS bundles under {world_dir}; nothing to catalog. This "
            f"is a real answer for an unmodded world and a misconfiguration for "
            f"this one")
    tokens, failures = scan(bundles, progress)
    # `.prefab` / `.asset` stems, the same widening `CatalogFromFiles` does on a
    # SoftRef manifest: a bundle indexes assets by path and a prefab's name is the
    # file's stem, so without this a token is "grausten_wall.prefab" and the hash
    # on the ZDO never matches.
    widened = set(tokens)
    for token in tokens:
        for ending in (".prefab", ".asset"):
            if token.endswith(ending) and len(token) > len(ending) + 2:
                widened.add(token[: -len(ending)])
        if token.startswith("_") and len(token) > 4:
            widened.add(token[1:])
    target = out or names_path(world)
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(widened)
    target.write_text("\n".join(ordered) + "\n")
    return {
        "world": world,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bundles": len(bundles),
        "bundle_roots": notes,
        "tokens": len(tokens),
        "names_written": len(ordered),
        "unreadable_bundles": failures,
        "out": str(target),
    }


def resolve(snapshot_path: Path, names: Path) -> dict:
    """How many of a snapshot's unresolved prefab hashes this name dump names.

    Reads `health.unknown_prefab_sample` and `health.unknown_prefab_kinds` -- the
    census `worldintel` publishes precisely because an unresolved object is dropped
    from the object list and its hash is unrecoverable afterwards.
    """
    snap = json.loads(snapshot_path.read_text())
    health = snap.get("health") or {}
    sample = health.get("unknown_prefab_sample") or []
    if not sample:
        raise SystemExit(
            f"{snapshot_path} carries no `health.unknown_prefab_sample`. Either the "
            f"world has no unresolved hashes, or it was analysed by a build without "
            f"the census -- and those two are not the same answer")
    by_hash: dict[int, str] = {}
    for line in names.read_text().splitlines():
        name = line.strip()
        if name:
            by_hash.setdefault(stable_hash(name), name)
    resolved, unresolved = [], []
    for row in sample:
        name = by_hash.get(row["prefab_hash"])
        (resolved if name else unresolved).append(
            {**row, **({"name": name} if name else {})})
    covered = sum(r["objects"] for r in resolved)
    total = sum(r["objects"] for r in sample)
    return {
        "snapshot": str(snapshot_path),
        "names": str(names),
        "candidate_names": len(by_hash),
        "unknown_prefab_objects_world": health.get("unknown_prefabs"),
        "unknown_prefab_kinds_world": health.get("unknown_prefab_kinds"),
        "census_rows": len(sample),
        "census_objects": total,
        "resolved_kinds": len(resolved),
        "resolved_objects": covered,
        "resolved": resolved,
        "still_unresolved": unresolved,
    }


def _cmd_build(args: argparse.Namespace) -> int:
    report = build(Path(args.valheim_root), args.world,
                   Path(args.out) if args.out else None,
                   progress=lambda line: print(line, flush=True))
    print(json.dumps({k: v for k, v in report.items()
                      if k != "unreadable_bundles"}, indent=1))
    bad = report["unreadable_bundles"]
    print(f"unreadable bundles: {len(bad)}")
    for line in bad[:10]:
        print(f"  !! {line}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    report = resolve(Path(args.snapshot), Path(args.names))
    print(f"candidate names            : {report['candidate_names']}")
    print(f"world unresolved objects   : {report['unknown_prefab_objects_world']}")
    print(f"world unresolved kinds     : {report['unknown_prefab_kinds_world']}")
    print(f"census rows (bounded)      : {report['census_rows']} "
          f"covering {report['census_objects']} objects")
    print(f"RESOLVED by this name dump : {report['resolved_kinds']} kinds, "
          f"{report['resolved_objects']} objects")
    print(f"STILL UNRESOLVED           : "
          f"{report['census_rows'] - report['resolved_kinds']} kinds, "
          f"{report['census_objects'] - report['resolved_objects']} objects")
    print("\nresolved (commonest first):")
    for row in report["resolved"][:40]:
        print(f"  {row['prefab_hash']:>12}  {row['objects']:>6}  {row['name']}")
    if report["still_unresolved"]:
        print("\nSTILL UNRESOLVED -- this is where the next invisible thing hides:")
        for row in report["still_unresolved"]:
            print(f"  {row['prefab_hash']:>12}  {row['objects']:>6}  (no name)")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
        print(f"\nwrote {args.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="walk the deployed bundles and write the name dump")
    b.add_argument("--valheim-root", required=True)
    b.add_argument("--world", required=True)
    b.add_argument("--out", default=None)
    b.set_defaults(func=_cmd_build)

    r = sub.add_parser("resolve", help="how much of a snapshot's census this dump names")
    r.add_argument("--snapshot", required=True)
    r.add_argument("--names", required=True)
    r.add_argument("--json", default=None)
    r.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
