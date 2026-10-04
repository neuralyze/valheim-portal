#!/usr/bin/env python3
"""INDEPENDENT WHOLE-SAVE CENSUS OF A DECK, by prefab and FULL 3D POSITION.

WHY THIS EXISTS AND WHY IT IS NOT `boardwalk`'s POSTCONDITION.  `boardwalk`
proves a landing with `objects_count id=<prefab> pos=<x>,<z> max=<r>`, which is
a Y-BLIND XZ CYLINDER counted through the same RCON session that sent the
pieces.  It answers "did the count in this disc go up by the number I sent",
which a neighbouring structure, a duplicated send or a piece landed at the
wrong HEIGHT all satisfy.  THE REFERENCE MUST NOT COME FROM THE ARTEFACT UNDER
TEST: this reads the SAVE'S OWN ZDO TABLE, written by the server and re-read
from disk, and matches every planned piece to a ZDO by PREFAB PLUS FULL 3D
POSITION.

AND IT ASSERTS THE WHOLE BATCH.  A sampled postcondition certifies the sample
and asserts nothing about the rest, so the verdict here is over EVERY piece the
plan contains, with the unmatched ones NAMED.

ZDO IDS ARE NOT DURABLE, which is why the key is prefab + position and never an
id.

  deck_census.py corridor --zdos <zdos.tsv> --span <crossing_id>
  deck_census.py match    --zdos <zdos.tsv> --span <crossing_id>
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "crossings"))
sys.path.insert(0, str(HERE.parent / "terraform"))

import boardwalk as bw                    # noqa: E402

# A spawned piece lands at the coordinates it was sent; the save round-trips
# them through a float32.  0.05 m is two orders of magnitude above that and
# two orders BELOW the 2 m station pitch, so it cannot merge two pieces.
MATCH_TOL_M = 0.05
# The corridor a deck occupies, the same numbers `boardwalk` builds to.
HALF_W_M = bw.DECK_HALF_WIDTH_M
OVERHANG_M = bw.CORRIDOR_OVERHANG_M
# Prefabs a boardwalk is made of.  Named rather than pattern-matched so a
# census cannot quietly widen to somebody else's structure.
DECK_PREFABS = ("wood_floor", "wood_pole", "wood_beam", "wood_pole_log_4",
                "wood_wall_log_4x0.5")


def load_zdos(path: Path) -> list[tuple[str, float, float, float]]:
    rows = []
    with path.open() as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 9 or not f[1]:
                continue
            rows.append((f[1], float(f[2]), float(f[3]), float(f[4])))
    return rows


def span_by_id(cid: str) -> dict:
    spans, _ = bw.spans_from_network()
    spans = bw.project_onto_centreline(spans)
    for s in spans:
        if s["crossing_id"] == cid:
            return s
    raise SystemExit(f"no such crossing: {cid}; have "
                     f"{sorted(s['crossing_id'] for s in spans)}")


def in_corridor(span: dict, x: float, z: float,
                half_w: float = HALF_W_M) -> bool:
    c = bw.corridor_of(span, half_w, OVERHANG_M)
    return bw._inside(c, x, z)


def corridor_census(rows, span: dict) -> dict:
    hit = Counter()
    for nm, x, y, z in rows:
        if in_corridor(span, x, z):
            hit[nm] += 1
    return dict(crossing_id=span["crossing_id"],
                total_zdos_in_save=len(rows),
                pieces_in_corridor=sum(hit.values()),
                by_prefab=dict(sorted(hit.items())),
                deck_pieces_in_corridor=sum(
                    v for k, v in hit.items() if k in DECK_PREFABS),
                half_width_m=HALF_W_M, overhang_m=OVERHANG_M)


def planned_pieces(span: dict, actor: str, out: Path) -> list:
    prepared, _zones, _surface = bw.prepare([span], actor, out)
    pr = prepared[0]
    if pr.get("generator_refusal"):
        raise SystemExit(f"{span['crossing_id']}: the generator refuses, so "
                         f"there is no plan to match: {pr['generator_refusal']}")
    return pr["pieces"]


def match(rows, span: dict, pieces: list) -> dict:
    """EVERY planned piece against the save, by prefab + full 3D position."""
    buckets: dict[str, list[tuple[float, float, float]]] = {}
    for nm, x, y, z in rows:
        buckets.setdefault(nm, []).append((x, y, z))
    used: set[tuple[str, int]] = set()
    matched, missing = [], []
    worst = 0.0
    for p in pieces:
        cands = buckets.get(p.prefab, ())
        best_i, best_d = None, 1e9
        for i, (x, y, z) in enumerate(cands):
            if (p.prefab, i) in used:
                continue
            d = math.dist((p.x, p.y, p.z), (x, y, z))
            if d < best_d:
                best_i, best_d = i, d
        if best_i is not None and best_d <= MATCH_TOL_M:
            used.add((p.prefab, best_i))
            matched.append(best_d)
            worst = max(worst, best_d)
        else:
            missing.append(dict(prefab=p.prefab, role=p.role,
                                xyz=[round(p.x, 3), round(p.y, 3),
                                     round(p.z, 3)],
                                nearest_same_prefab_m=(
                                    None if best_i is None else round(best_d, 3))))
    return dict(crossing_id=span["crossing_id"],
                planned=len(pieces), matched=len(matched),
                missing=len(missing), missing_rows=missing[:12],
                worst_match_offset_m=round(worst, 4),
                tolerance_m=MATCH_TOL_M,
                whole_batch=True,
                instrument="roads/deck_census.py::match over the save's own "
                           "ZDO table, keyed on prefab + FULL 3D position, "
                           "one ZDO consumed per planned piece")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("op", choices=["corridor", "match"])
    ap.add_argument("--zdos", required=True)
    ap.add_argument("--span", required=True)
    ap.add_argument("--actor", default="BuildDecks")
    ap.add_argument("--out", default="/tmp/deck_census")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = load_zdos(Path(args.zdos))
    span = span_by_id(args.span)
    c = corridor_census(rows, span)
    print(f"{c['crossing_id']}: {c['pieces_in_corridor']} ZDO(s) of "
          f"{c['total_zdos_in_save']} in the save lie inside the "
          f"{c['half_width_m']:.3f} m corridor "
          f"(+{c['overhang_m']:g} m each end); "
          f"{c['deck_pieces_in_corridor']} of them are boardwalk prefabs")
    for k, v in c["by_prefab"].items():
        print(f"    {k:26s} {v}")
    if args.op == "corridor":
        return 0
    pieces = planned_pieces(span, args.actor, out)
    m = match(rows, span, pieces)
    print(f"\n{m['crossing_id']}: {m['matched']} of {m['planned']} planned "
          f"piece(s) found in the save by prefab + full 3D position "
          f"(tolerance {m['tolerance_m']} m, worst offset "
          f"{m['worst_match_offset_m']} m)")
    for r in m["missing_rows"]:
        print(f"    MISSING {r['prefab']:22s} {r['role']:34s} {r['xyz']} "
              f"nearest same prefab {r['nearest_same_prefab_m']} m")
    if m["missing"]:
        print(f"\n{m['missing']} of {m['planned']} planned piece(s) are NOT "
              f"in the save.")
        return 4
    print(f"\nALL {m['planned']} planned piece(s) are in the save.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
