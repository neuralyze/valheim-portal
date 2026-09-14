#!/usr/bin/env python3
"""Materialise blueprint bodies from the local corpus into a preset's blueprints/.

The bodies are deliberately **not** vendored into this repository: no licence is
stated for any file by any of their authors, and this repo is published.  So
`data/corpus_manifest.json` carries the identity (author, path, size, SHA-256,
viability verdict) and this script copies the bytes in at use time, verifying
every checksum as it goes.

    # copy what one preset's placements.yaml asks for
    ./materialise.py --world ../worlds/Ulfsland --preset deepnorth-sandbox

    # every preset, and fail loudly if the corpus has drifted
    ./materialise.py --world ../worlds/Ulfsland --all --strict

    # just check: is the corpus still there and unchanged?
    ./materialise.py --verify-corpus

Why the checksums matter: the corpus lives at
``/media/big4/projects/game/valheim/old/old/old/old_Storgard/config_merged/BepInEx/PlanBuild/blueprints``.
A path four ``old``s deep is exactly what a future cleanup deletes, and these
digests are what will tell us it happened.

Infinity Hammer reads blueprints recursively from ``BepInEx/config/PlanBuild/``
and uses the **containing folder name as the blueprint's category** (MEASURED:
``HammerBlueprintCommand::GetFolderNameFromPath``).  So when deploying to the
`ulfsland-admin` client, copy a preset's ``blueprints/`` to
``BepInEx/config/PlanBuild/<preset>/`` and the whole preset shows up as one
category in ``hammer_menu blueprints``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> dict:
    doc = json.loads(path.read_text("utf-8"))
    best: dict[str, dict] = {}
    for e in doc["entries"]:
        if not e["sha256"]:
            continue
        prev = best.get(e["file"])
        if prev is None or e["bytes"] > prev["bytes"]:
            best[e["file"]] = e
    return best


def verify_corpus(manifest: dict) -> int:
    missing = changed = ok = 0
    for name, e in sorted(manifest.items()):
        src = Path(e["source"])
        if not src.exists():
            print(f"  MISSING  {name}  ({src})")
            missing += 1
            continue
        digest = sha256(src)
        if digest != e["sha256"]:
            print(f"  CHANGED  {name}  manifest={e['sha256'][:12]} disk={digest[:12]}")
            changed += 1
        else:
            ok += 1
    print(f"corpus: {ok} unchanged, {changed} changed, {missing} missing, of {len(manifest)}")
    return 1 if (changed or missing) else 0


def materialise(world: Path, preset: str, manifest: dict, strict: bool) -> int:
    pfile = world / preset / "placements.yaml"
    if not pfile.exists():
        print(f"  no placements.yaml for {preset}", file=sys.stderr)
        return 1
    doc = yaml.safe_load(pfile.read_text("utf-8"))
    dest = world / preset / "blueprints"
    dest.mkdir(parents=True, exist_ok=True)

    problems = 0
    for p in doc.get("placements") or []:
        name = p["blueprint"]
        want = p.get("sha256", "")
        e = manifest.get(name)
        if e is None:
            print(f"  !! {preset}/{name}: not in the manifest")
            problems += 1
            continue
        src = Path(e["source"])
        if not src.exists():
            print(f"  !! {preset}/{name}: source gone ({src})")
            problems += 1
            continue
        digest = sha256(src)
        if want and digest != want:
            print(
                f"  !! {preset}/{name}: placements.yaml pins {want[:12]} "
                f"but the corpus now holds {digest[:12]}"
            )
            problems += 1
            if strict:
                continue
        out = dest / name
        shutil.copy2(src, out)
        print(f"  {preset}/{name}  {e['bytes']:>9} bytes  {e['verdict']}  by {e['creator'] or '?'}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=str(HERE / "data" / "corpus_manifest.json"))
    ap.add_argument("--world", help="tools/jumpstart/worlds/<World>")
    ap.add_argument("--preset")
    ap.add_argument("--all", action="store_true", help="every preset under --world")
    ap.add_argument("--verify-corpus", action="store_true")
    ap.add_argument("--strict", action="store_true", help="refuse to copy on checksum mismatch")
    args = ap.parse_args(argv)

    manifest = load_manifest(Path(args.manifest))

    if args.verify_corpus:
        return verify_corpus(manifest)

    if not args.world:
        ap.error("--world is required unless --verify-corpus")
    world = Path(args.world)

    if args.all:
        presets = [p.name for p in sorted(world.iterdir()) if (p / "placements.yaml").exists()]
    elif args.preset:
        presets = [args.preset]
    else:
        ap.error("pass --preset or --all")

    problems = 0
    for preset in presets:
        problems += materialise(world, preset, manifest, args.strict)
    if problems:
        print(f"{problems} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
