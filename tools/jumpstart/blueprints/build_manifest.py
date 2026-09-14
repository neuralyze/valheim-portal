#!/usr/bin/env python3
"""Build the blueprint corpus manifest: reference, never vendor.

This repository is published and already treats third-party material as
something to *reference* with a pinned identity rather than copy in --
`deploy/upstream-sources.json` is the same pattern.  The blueprint corpus is
~112 PlanBuild files by named individual authors with **no licence stated for
any of them**, so the bodies stay on disk and this manifest is what the repo
carries.

Per file the manifest records:

  * `file`, `source` -- name and the absolute path it lives at,
  * `bytes`, `sha256` -- so a future cleanup that deletes the source directory
    is detectable rather than silent (the corpus path is four `old`s deep),
  * `creator` -- the author string out of the blueprint's own `#Creator:` header,
  * the inventory verdict (`PLACES_CLEAN` / `PLACES_WITH_GAPS` / `EMPTY` / ...),
    piece count, distinct prefab count, footprint and missing-prefab detail,
  * `payload` -- `base` or `exploit`, with loot/creature object counts.  Three
    files in this corpus are loot-spam payloads carrying hundreds of
    `TreasureChest_*` objects, not buildings; they are marked so nobody
    selects one by name later.

The viability verdict is the expensive part: it cannot be re-derived from the
files alone, only by resolving every prefab name against Valheim 1.0.12's own
asset bundles plus this fleet's deployed mod DLLs.

Usage
-----
    python3 inventory.py <corpus dirs...> --json data/inventory.json
    python3 build_manifest.py --inventory data/inventory.json \\
        --out data/corpus_manifest.json --tsv data/corpus_manifest.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Files whose object list shows they are loot payloads, not bases. Flagged by
# `inventory.py`'s `loot_exploit`, restated here by name so the judgement is
# explicit in the manifest rather than implied by a threshold.
KNOWN_EXPLOITS = {
    "15643-Valheimian.blueprint",
    "14464-Valheimian.blueprint",
    "gnome_house.blueprint",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", default=str(HERE / "data" / "inventory.json"))
    ap.add_argument("--out", default=str(HERE / "data" / "corpus_manifest.json"))
    ap.add_argument("--tsv", default=str(HERE / "data" / "corpus_manifest.tsv"))
    args = ap.parse_args(argv)

    reports = json.loads(Path(args.inventory).read_text("utf-8"))

    entries = []
    for r in reports:
        src = Path(r["path"])
        if not src.exists():
            digest = ""
            size = 0
            present = False
        else:
            digest = sha256(src) if src.stat().st_size else ""
            size = src.stat().st_size
            present = True
        exploit = r["file"] in KNOWN_EXPLOITS or r.get("loot_exploit", False)
        entries.append(
            {
                "file": r["file"],
                "source": str(src),
                "source_present": present,
                "bytes": size,
                "sha256": digest,
                "format": r["format"],
                "creator": r.get("creator", ""),
                "blueprint_name": r.get("name", ""),
                "verdict": r["verdict"],
                "pieces": r["pieces"],
                "distinct_prefabs": r["distinct_prefabs"],
                "footprint_xzy": r["footprint_xzy"],
                "missing_pct": r["missing_pct"],
                "missing_prefabs": r.get("missing_prefabs", {}),
                "mod_prefabs": r.get("mod_prefabs", {}),
                "loot_objects": r.get("loot_count", 0),
                "creature_objects": r.get("creature_count", 0),
                "ward_pieces": r.get("ward_pieces", 0),
                "payload": "exploit" if exploit else "base",
                "licence": "none stated",
            }
        )

    # stable order, and dedupe identical content appearing in both corpus dirs
    entries.sort(key=lambda e: (e["file"].lower(), e["source"]))

    by_digest: dict[str, list[str]] = {}
    for e in entries:
        if e["sha256"]:
            by_digest.setdefault(e["sha256"], []).append(e["source"])
    dupes = {d: p for d, p in by_digest.items() if len(p) > 1}

    tally: dict[str, int] = {}
    for e in entries:
        tally[e["verdict"]] = tally.get(e["verdict"], 0) + 1

    doc = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": (
            "REFERENCE ONLY -- the blueprint bodies are deliberately NOT vendored into "
            "this repository. No licence is stated for any file by any author, so they "
            "are local-use material. Materialise them with materialise.py, which "
            "verifies these checksums."
        ),
        "licence": "none stated for any entry; individual authors retain copyright",
        "viability_basis": (
            "every prefab name resolved against Valheim 1.0.12 asset bundles "
            "(StreamingAssets/SoftRef/Bundles) plus this fleet's deployed BepInEx "
            "plugin DLLs; see scan_piece_prefabs.py and data/prefab_evidence.json"
        ),
        "verdict_tally": tally,
        "duplicate_content": dupes,
        "entries": entries,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    print(f"wrote {out} ({len(entries)} entries)")
    print("verdicts:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print(f"duplicate content groups: {len(dupes)}")

    if args.tsv:
        cols = [
            "file",
            "format",
            "creator",
            "verdict",
            "payload",
            "pieces",
            "distinct_prefabs",
            "missing_pct",
            "loot_objects",
            "creature_objects",
            "bytes",
            "sha256",
            "licence",
            "source",
        ]
        lines = ["\t".join(cols)]
        for e in entries:
            lines.append("\t".join(str(e[c]) for c in cols))
        Path(args.tsv).write_text("\n".join(lines) + "\n", "utf-8")
        print(f"wrote {args.tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
