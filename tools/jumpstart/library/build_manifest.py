#!/usr/bin/env python3
"""Merge the corpus and web halves into one library manifest.

    classify.py   ->  data/corpus_roles.json   87 bodies on this fleet's own disk
    web.py        ->  data/web_roles.json     452 bodies pinned to commits or archive hashes
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
    "name",            # LIBRARY HANDLE -- unique across the manifest, see identity()
    "source_name",     # the file name at the source; what to look for on disk
    "category",        # base bridge portal outpost production defence flavour dock fixture
    "kind",            # module | setpiece | exploit | empty
    "verdict",         # PLACES_CLEAN | PLACES_WITH_GAPS | UNUSABLE | EMPTY
    "resolvable",      # is there a body this row can claim by hash?
    "metadata",        # curated (a human verdict) | derived (computed from measurements)
    "pieces",
    "footprint",       # x by z by y, metres
    "top_tier",        # highest progression tier its materials require
    "format",
    "creator",
    "licence",
    "body",            # committed | reference
    "origin",
    "origins",         # every source that carries this exact body
    "sha256",
    "role",
    "fit",
    "gaps",            # missing-prefab detail, or ""
)


def identity(rows: list[dict]) -> list[dict]:
    """Give every distinct BODY exactly one handle, and every handle one body.

    Two defects are fixed here, and they are opposite shapes of the same
    mistake -- treating a file NAME as an identity.

    1. `brokkr-the-cathedral.blueprint` occurs in two sources and is **two
       different buildings**: 8269 pieces / sha `3060609d536b` in
       `Oosquai/SavheimIV`, 8240 pieces / sha `8127bac49f36` in
       `offsetkeyz/valheim_mod_sync` (MEASURED). A name-keyed lookup resolved
       whichever row or root it reached first, so which of two 8000-piece
       cathedrals you got depended on directory order. Colliding names are
       therefore SUFFIXED with the first 12 hex of their own content hash, which
       makes the handle a function of the bytes rather than of the source.

    2. `salty-dick-cottage-final.blueprint` also occurs twice -- and is
       byte-identical, sha `6b588d9c46e2` both times (MEASURED). That is ONE
       body reachable from two places, not two bodies, so the rows are MERGED
       and both sources recorded in `origins`. Counting it twice inflated the
       library by a row that no consumer could ever ask for separately.

    Unresolvable rows carry no hash (see `classify.sha256`), so they cannot be
    grouped by content and are keyed by name alone -- which is safe, because the
    corpus half keys on filename and cannot produce two of them.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (row["name"], row["sha256"] or f"unresolved:{row['name']}")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    hashes_per_name: dict[str, set[str]] = {}
    for name, content in order:
        hashes_per_name.setdefault(name, set()).add(content)

    out: list[dict] = []
    for key in order:
        members = groups[key]
        # Prefer a committed body as the primary row: its `origin` is the one
        # `materialise.py` can satisfy without a fetch.
        primary = min(members, key=lambda r: (r["body"] != "committed", r["origin"]))
        merged = dict(primary)
        merged["source_name"] = primary["name"]
        merged["origins"] = ", ".join(sorted({m["origin"] for m in members}))
        name, _content = key
        if len(hashes_per_name[name]) > 1:
            stem, _, ext = name.rpartition(".")
            merged["name"] = f"{stem}__{primary['sha256'][:12]}.{ext}"
        out.append(merged)
    return out


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
                "resolvable": e.get("resolvable", bool(e["sha256"])),
                "unresolved_reason": e.get("unresolved_reason", ""),
                "metadata": e.get("metadata", "curated"),
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
                "resolvable": e.get("resolvable", bool(e["sha256"])),
                "unresolved_reason": e.get("unresolved_reason", ""),
                "metadata": e.get("metadata", "curated"),
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

    rows = identity(corpus_rows() + web_rows())
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

    handles = Counter(r["name"] for r in rows)
    clashing = sorted(h for h, n in handles.items() if n > 1)
    if clashing:
        raise SystemExit(
            "identity() failed to make handles unique -- two different bodies still "
            "answer to one name: " + ", ".join(clashing)
        )

    payload = {
        "generated_by": "tools/jumpstart/library/build_manifest.py",
        "policy": (
            "One row per distinct BODY. `name` is the library handle and is unique: "
            "two sources carrying byte-identical content are merged into one row with "
            "both listed in `origins`, and two different bodies sharing a file name are "
            "suffixed with the first 12 hex of their own content hash. `source_name` is "
            "the file name at the source. `body: committed` means the source states a "
            "licence that permits redistribution and the file lives under bodies/. "
            "`body: reference` means the body is NOT in this repo -- materialise.py "
            "copies it at use time and verifies the sha256 recorded here. "
            "`resolvable: false` means there is NO body to claim: the row records a name "
            "the corpus once held and nothing else, and no consumer should count it. "
            "See PROVENANCE.md for the licence position per source."
        ),
        "columns": list(COLUMNS),
        "totals": {
            "rows": len(rows),
            "resolvable_bodies": sum(1 for r in rows if r["resolvable"]),
            "unresolvable_rows": sum(1 for r in rows if not r["resolvable"]),
            "by_category": dict(sorted(Counter(r["category"] for r in rows).items())),
            "by_kind": dict(sorted(Counter(r["kind"] for r in rows).items())),
            "by_body": dict(sorted(Counter(r["body"] for r in rows).items())),
            "by_licence": dict(sorted(Counter(r["licence"] for r in rows).items())),
            "by_metadata": dict(sorted(Counter(r["metadata"] for r in rows).items())),
            "web_verdicts": dict(
                sorted(Counter(r["verdict"] for r in rows if r["origin"] != "fleet corpus").items())
            ),
            "non_base_rows": sum(1 for r in rows if r["category"] not in ("base", "fixture")),
            "repeatable_modules": sum(1 for r in rows if r["kind"] == "module"),
            "renamed_for_collision": sorted(
                f"{r['source_name']} -> {r['name']}" for r in rows
                if r["name"] != r["source_name"]
            ),
            "merged_duplicates": sorted(
                f"{r['name']} <- {r['origins']}" for r in rows if ", " in r["origins"]
            ),
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
