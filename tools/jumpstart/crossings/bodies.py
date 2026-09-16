#!/usr/bin/env python3
"""Shortlist library bodies that belong OVER WATER, and say why.

The operator's rule, verbatim in the brief: uneven bases are a DEFECT on land
and EXPECTED over water. `tools/jumpstart/library/audit_bases.py` already
encodes that as `flat_pad_verdict`, so this file does not re-derive
groundedness -- it SELECTS from the audit and adds the two things a crossing
needs and the audit does not carry:

  * the WATERLINE the body wants. A pier body is designed with its piles
    hanging below its deck; the number that matters when placing it over water
    is how far its lowest solid sits below its own datum
    (`base_y - bottom_solid_y`), because that is the draught the water has to be
    deep enough for.
  * a ZDO cost, which is simply the piece count. This is the whole feasibility
    question for a network: `dock.blueprint` is 2036 ZDOs and Hrafnheim's entire
    persistent object count was 1366.

Run with no arguments for the full over-water shortlist.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "library" / "data" / "base_audit.json"
MANIFEST = ROOT / "library" / "data" / "library_manifest.json"

# Names that describe a structure whose job is to meet water. Matched against
# the filename, which is the only nomenclature the corpus carries.
WATER_WORDS = re.compile(
    r"dock|harbou?r|harbor|pier|jetty|quay|wharf|boat|ship|shipyard|marina|"
    r"fisher|fishing|lighthouse|bridge|stilt|hafen|port",
    re.I)


def load():
    audit = json.loads(AUDIT.read_text())
    manifest = json.loads(MANIFEST.read_text())
    by_name = {e["name"]: e for e in manifest["entries"]}
    return audit["entries"], by_name


def shortlist(entries, by_name, *, max_pieces: int | None = None,
              settings=("needs_support", "supported_air", "low_plinth"),
              require_word: bool = False):
    out = []
    for e in entries:
        name = e["name"]
        if e.get("setting") not in settings:
            continue
        if max_pieces is not None and (e.get("pieces") or 0) > max_pieces:
            continue
        worded = bool(WATER_WORDS.search(name))
        if require_word and not worded:
            continue
        base_y = e.get("base_y")
        bottom = e.get("bottom_solid_y")
        draught = None
        if base_y is not None and bottom is not None:
            draught = round(base_y - bottom, 3)
        out.append(dict(
            name=name,
            category=by_name.get(name, {}).get("category"),
            pieces=e.get("pieces"),
            footprint=[e.get("footprint_x_m"), e.get("footprint_z_m")],
            setting=e.get("setting"),
            verdict=e.get("flat_pad_verdict"),
            base_y=base_y,
            bottom_solid_y=bottom,
            draught_m=draught,
            floating_fraction=e.get("floating_fraction"),
            max_air_m=e.get("max_air_m"),
            pile_pieces=e.get("pile_pieces"),
            columns=e.get("columns"),
            water_word=worded,
            portals=e.get("portals"),
            stations=e.get("stations"),
        ))
    out.sort(key=lambda r: (not r["water_word"], r["pieces"] or 0))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pieces", type=int, default=None)
    ap.add_argument("--only-water-words", action="store_true")
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args(argv)
    entries, by_name = load()
    rows = shortlist(entries, by_name, max_pieces=args.max_pieces,
                     require_word=args.only_water_words)
    print(json.dumps(rows[:args.top], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
