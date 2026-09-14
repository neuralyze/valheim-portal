#!/usr/bin/env python3
"""Harvest the set of prefab-shaped names present in the Valheim 1.0.12 install.

Evidence source, in priority order:

1. ``valheim_server_Data/StreamingAssets/SoftRef/Bundles`` -- 796 UnityFS
   bundles holding every addressable prefab the game ships, including build
   pieces.  A prefab name appears verbatim as an ASCII token in the serialized
   object file of the bundle that owns it.
2. ``config_merged/bepinex/plugins`` -- every deployed mod DLL, scanned for the
   same token shape.  This is what lets us tell "removed by Iron Gate" apart
   from "supplied by a mod this fleet happens to have".

The reader is reused from the sibling module ``extract_prefab_names.py`` (same
directory tree, not modified by this tool).

Output JSON:

    {
      "generated_utc": "...",
      "vanilla": [...],           # tokens found in the game bundles
      "mods": {"<PluginDir>": [...]},   # tokens found only in that mod's DLLs
      "counts": {...}
    }

Usage:
    python3 scan_piece_prefabs.py --out data/piece_prefabs.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIBLING = HERE.parent / "extract_prefab_names.py"

# Two passes. The plain pass is the sibling module's shape. The dotted pass
# exists because a handful of real Valheim piece prefabs carry a decimal in the
# name -- `wood_wall_log_4x0.5` is the load-bearing example, it is the single
# most common piece in the community blueprint corpus, and a regex without `.`
# reports it as removed. Trailing dots are stripped so sentence punctuation in
# strings does not leak into the evidence set.
TOKEN_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,63}")
DOTTED_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")


def _tokens(blob: bytes) -> set[bytes]:
    out = {m.group(0) for m in TOKEN_RE.finditer(blob)}
    for m in DOTTED_RE.finditer(blob):
        tok = m.group(0).rstrip(b".")
        if 3 <= len(tok) <= 64:
            out.add(tok)
    return out


def _load_sibling():
    spec = importlib.util.spec_from_file_location("_jumpstart_extract", SIBLING)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SIBLING}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scan_bundles(bundle_dir: Path, iter_blocks, log) -> set[str]:
    tokens: set[bytes] = set()
    files = sorted(p for p in bundle_dir.iterdir() if p.is_file())
    for index, path in enumerate(files, 1):
        try:
            for block in iter_blocks(path):
                tokens |= _tokens(block)
        except Exception as exc:  # one bad bundle must not abort the scan
            log(f"  !! {path.name}: {exc}")
        if index % 100 == 0:
            log(f"  .. {index}/{len(files)} bundles, {len(tokens)} tokens")
    return {t.decode("ascii", "replace") for t in tokens}


def scan_mod_dlls(plugins: Path, log) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for dll in sorted(plugins.rglob("*.dll")):
        try:
            blob = dll.read_bytes()
        except OSError as exc:
            log(f"  !! {dll}: {exc}")
            continue
        # attribute the DLL to its top-level plugin directory
        rel = dll.relative_to(plugins)
        owner = rel.parts[0] if len(rel.parts) > 1 else rel.name
        names = _tokens(blob)
        # UTF-16LE literals are the common case for C# string constants
        names |= _tokens(blob.replace(b"\x00", b""))
        names = {t.decode("ascii", "replace") for t in names}
        out.setdefault(owner, set()).update(names)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--valheim-root", default="/media/big4/projects/game/valheim")
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--out", default=str(HERE / "data" / "piece_prefabs.json"))
    ap.add_argument("--skip-mods", action="store_true")
    args = ap.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    root = Path(args.valheim_root) / args.world
    bundle_dir = root / "data" / "server" / "valheim_server_Data" / "StreamingAssets" / "SoftRef" / "Bundles"
    plugins = root / "config_merged" / "bepinex" / "plugins"

    sibling = _load_sibling()

    log(f"scanning bundles: {bundle_dir}")
    vanilla = scan_bundles(bundle_dir, sibling.iter_bundle_blocks, log)
    log(f"vanilla tokens: {len(vanilla)}")

    mods: dict[str, list[str]] = {}
    if not args.skip_mods:
        log(f"scanning mod dlls: {plugins}")
        raw = scan_mod_dlls(plugins, log)
        for owner, names in sorted(raw.items()):
            only = sorted(names - vanilla)
            if only:
                mods[owner] = only
        log(f"mods with unique tokens: {len(mods)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "valheim_root": str(root),
                "bundle_dir": str(bundle_dir),
                "plugins_dir": str(plugins),
                "vanilla": sorted(vanilla),
                "mods": mods,
                "counts": {
                    "vanilla": len(vanilla),
                    "mods": {k: len(v) for k, v in mods.items()},
                },
            },
            indent=1,
        )
        + "\n",
        "utf-8",
    )
    log(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
