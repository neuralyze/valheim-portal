#!/usr/bin/env python3
"""Merge the corpus and web halves into one library manifest.

    classify.py   ->  data/corpus_roles.json   86 files on this fleet's own disk
    web.py        ->  data/web_roles.json      90 files pinned to public commits
    build_manifest.py ->  data/library_manifest.{json,tsv}

One row per blueprint, with the fields the library is actually consulted by:
identity, licence status, whether the body is committed, viability verdict,
category, module-or-set-piece, tier fit and SHA-256.

Usage:
    ./build_manifest.py
    ./build_manifest.py --category bridge portal outpost   # filter the table
    ./build_manifest.py --modules-only
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

COLUMNS = (
    "name",            # file name, the handle Infinity Hammer takes
    "category",        # base bridge portal outpost production defence flavour dock fixture
    "kind",            # module | setpiece | exploit | empty
    "verdict",         # PLACES_CLEAN | PLACES_WITH_GAPS | UNUSABLE | EMPTY
    "pieces",
    "footprint",       # x by z by y, metres
    "top_tier",        # highest progression tier its materials require
    "format",
    "creator",
    "licence",
    "body",            # committed | reference
    "origin",
    "sha256",
    "role",
    "fit",
    "gaps",            # missing-prefab detail, or ""
)


def gaps_text(entry: dict) -> str:
    bits = []
    missing = entry.get("missing_prefabs") or {}
    mods = entry.get("mod_prefabs") or {}
    if missing:
        bits.append(
            f"{entry.get('missing_pct', 0)}% missing: "
            + ", ".join(f"{k} x{v}" for k, v in list(missing.items())[:6])
        )
    if mods:
        bits.append(
            "mod-supplied: "
            + ", ".join(f"{k} ({v[1]})" for k, v in list(mods.items())[:6])
        )
    if entry.get("ih_discarded_rows"):
        bits.append(
            f"{entry['ih_discarded_rows']} terrain rows discarded by Infinity Hammer "
            f"({', '.join(entry.get('unknown_sections') or ['#Terrain'])}) -- level the "
            "ground by hand, and strip them before the RCON route"
        )
    if entry.get("legacy_terrain_rejected"):
        bits.append("LEGACY #Height/#Paint -- Infinity Hammer THROWS on this file")
    if entry.get("zdo_spam"):
        bits.append(
            "ZDO spam: " + ", ".join(f"{k} x{v}" for k, v in entry["zdo_spam"].items())
        )
    return "; ".join(bits)


def corpus_rows() -> list[dict]:
    payload = json.loads((DATA / "corpus_roles.json").read_text("utf-8"))
    out = []
    for e in payload["entries"]:
        fx, fz, fy = e["footprint_xzy"]
        out.append(
            {
                "name": e["file"],
                "category": e["category"],
                "kind": e["kind"],
                "verdict": e["verdict"],
                "pieces": e["pieces"],
                "footprint": f"{fx}x{fz}x{fy}",
                "top_tier": f"{e['top_tier']}({e['top_tier_pieces']})",
                "format": e["format"],
                "creator": e["creator"],
                "licence": "none stated",
                "body": "reference",
                "origin": "fleet corpus",
                "sha256": e["sha256"],
                "role": e["role"],
                "fit": e["fit"],
                "gaps": gaps_text(e),
            }
        )
    return out


def web_rows() -> list[dict]:
    payload = json.loads((DATA / "web_roles.json").read_text("utf-8"))
    out = []
    for e in payload["entries"]:
        fx, fz, fy = e["footprint_xzy"]
        out.append(
            {
                "name": e["file"],
                "category": e["category"],
                "kind": e["kind"],
                "verdict": e["verdict"],
                "pieces": e["pieces"],
                "footprint": f"{fx}x{fz}x{fy}",
                "top_tier": f"{e['top_tier']}({e['top_tier_pieces']})",
                "format": e["format"],
                "creator": e["creator"],
                "licence": e["licence"],
                "body": "committed" if e["committed_body"] else "reference",
                "origin": e["origin"],
                "sha256": e["sha256"],
                "role": e["role"],
                "fit": e["fit"],
                "gaps": gaps_text(e),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--category", nargs="*", help="filter the printed table")
    ap.add_argument("--modules-only", action="store_true")
    ap.add_argument("--print", action="store_true", dest="show")
    args = ap.parse_args()

    rows = corpus_rows() + web_rows()
    rows.sort(key=lambda r: (r["category"], r["kind"] != "module", -r["pieces"]))

    selected = rows
    if args.category:
        wanted = set(args.category)
        selected = [r for r in selected if r["category"] in wanted]
    if args.modules_only:
        selected = [r for r in selected if r["kind"] == "module"]

    table = "\n".join(
        ["\t".join(COLUMNS)]
        + [
            "\t".join(str(r[c]).replace("\t", " ").replace("\n", " ") for c in COLUMNS)
            for r in selected
        ]
    ) + "\n"

    if args.show or args.category or args.modules_only:
        print(table, end="")
        return 0

    payload = {
        "generated_by": "tools/jumpstart/library/build_manifest.py",
        "policy": (
            "One row per blueprint. `body: committed` means the source states a licence "
            "that permits redistribution and the file lives under bodies/. `body: "
            "reference` means the body is NOT in this repo -- materialise.py fetches or "
            "copies it at use time and verifies the sha256 recorded here. See "
            "PROVENANCE.md for the licence position per source."
        ),
        "columns": list(COLUMNS),
        "totals": {
            "rows": len(rows),
            "by_category": dict(sorted(Counter(r["category"] for r in rows).items())),
            "by_kind": dict(sorted(Counter(r["kind"] for r in rows).items())),
            "by_body": dict(sorted(Counter(r["body"] for r in rows).items())),
            "by_licence": dict(sorted(Counter(r["licence"] for r in rows).items())),
            "web_verdicts": dict(
                sorted(Counter(r["verdict"] for r in rows if r["origin"] != "fleet corpus").items())
            ),
            "non_base_rows": sum(1 for r in rows if r["category"] not in ("base", "fixture")),
            "repeatable_modules": sum(1 for r in rows if r["kind"] == "module"),
        },
        "entries": rows,
    }
    (DATA / "library_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )
    (DATA / "library_manifest.tsv").write_text(table, "utf-8")
    print(f"{len(rows)} rows -> data/library_manifest.json, data/library_manifest.tsv")
    for key, value in payload["totals"].items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
