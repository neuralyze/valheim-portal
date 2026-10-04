#!/usr/bin/env python3
"""Compact the raw PieceGeometry collider dump into `data/piece_geometry.json`.

Input is the TSV that `run_piecegeometry.sh` produces inside a sandbox
dedicated server: one row per collider, eight corner points, in the PREFAB
ROOT's local frame. See PieceGeometry.cs for how those corners are obtained and
why corners rather than centre+extent.

What this keeps, and what it drops, stated because a data file that quietly
drops the case you needed is the expensive kind of bug:

  KEPT    every non-trigger collider of every prefab, as either
            ["b", minx, miny, minz, maxx, maxy, maxz, KIND]   axis-aligned
            ["p", x0, y0, z0, ... x7, y7, z7, KIND]           oblique
          The `b` form is used only when the corner set really is axis-aligned
          in prefab-local space (at most two distinct values per axis), so it is
          lossless, not an approximation.
  KEPT    THE COLLIDER KIND, as the last element of every row: "box",
          "capsule", "sphere" or "mesh".  THE FIRST THREE ARE THE SHAPE
          ITSELF; "mesh" IS ONLY ITS BOUNDING BOX.  Dropping this was a real
          defect with a live verdict on it: `Beech1` carries two MeshCollider
          bounds and no capsule at all, so the corpus reported the tree as a
          19.08 x 21.50 m prefab-local box - 28-30 m once rotated - and the
          spawn gate refused a bed as "1.087 m INSIDE Beech1" with the tree's
          pivot 23.2 m away.  6,956 of the 11,972 non-render colliders in this
          dump are `mesh`, so this is 58 % of the corpus, not a corner case.
          A reader that cannot tell a shape from its bounding box cannot tell
          a measurement from an upper bound, and an upper bound must not
          decide a verdict.  The rows are otherwise BYTE-IDENTICAL to the
          kind-less corpus: the tag is appended, no corner moves.
  KEPT    prefabs with NO solid collider at all, as an empty list. That is the
          difference between "measured, has no solid" and "never measured",
          and the datum rule has to be able to tell them apart in order to
          refuse rather than guess.
  DROPPED trigger colliders. A trigger is an interaction volume, not geometry.
  DROPPED render meshes. The datum question is about solids.

Usage:
    VH_SRC=<server install> OUT=/tmp/piece_colliders.tsv \
        tools/jumpstart/blueprints/run_piecegeometry.sh
    tools/jumpstart/blueprints/build_piece_geometry.py /tmp/piece_colliders.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "data" / "piece_geometry.json"
ROUND = 4


def compact(rows) -> tuple[dict[str, list], dict[str, bool], dict[str, int]]:
    prefabs: dict[str, list] = {}
    is_piece: dict[str, bool] = {}
    tally = {"colliders": 0, "axis_aligned": 0, "oblique": 0, "unmeasurable": 0,
             "triggers_dropped": 0, "render_meshes_dropped": 0,
             # THE PRECISION SPLIT, counted so a reader can see how much of the
             # corpus is an upper bound rather than a shape.
             "tight_colliders": 0, "bounds_only_colliders": 0}
    for r in rows:
        name = r["name"]
        prefabs.setdefault(name, [])
        # `has_piece` separates BUILD pieces from world decor. It is load-bearing
        # for the floorless fallback: `Portal13.vbuild` contains a `Rock_4` whose
        # solid reaches 3.02 m below its pivot -- a rock modelled to be sunk into
        # terrain -- while every built piece in that body bottoms out at 0.066.
        # Taking the lowest solid over ALL objects would lift the whole shrine
        # 3 m into the air, which is the reported bug wearing a different hat.
        is_piece[name] = is_piece.get(name, False) or r["has_piece"] == "1"
        kind = r["kind"]
        if kind == "mesh_render":
            tally["render_meshes_dropped"] += 1
            continue
        if r["is_trigger"] == "1":
            tally["triggers_dropped"] += 1
            continue
        if kind in ("none", "mesh_null") or r["x0"] == "na":
            # `none` means the prefab has no collider component at all;
            # `mesh_null` means it has one whose mesh was not loadable. Both are
            # recorded by their ABSENCE from the solid list, and mesh_null is
            # counted so a caller can see that a measurement failed.
            if kind != "none":
                tally["unmeasurable"] += 1
            continue
        pts = [(float(r[f"x{i}"]), float(r[f"y{i}"]), float(r[f"z{i}"])) for i in range(8)]
        xs = {round(p[0], ROUND) for p in pts}
        ys = {round(p[1], ROUND) for p in pts}
        zs = {round(p[2], ROUND) for p in pts}
        tally["colliders"] += 1
        # "mesh" is the ONLY kind whose row is a bounding box rather than the
        # collider itself; box/capsule/sphere are written from their own shape
        # parameters by PieceGeometry.cs::ShapeBounds and are exact.
        if kind == "mesh":
            tally["bounds_only_colliders"] += 1
        else:
            tally["tight_colliders"] += 1
        if len(xs) <= 2 and len(ys) <= 2 and len(zs) <= 2:
            tally["axis_aligned"] += 1
            prefabs[name].append(["b", min(xs), min(ys), min(zs),
                                  max(xs), max(ys), max(zs), kind])
        else:
            tally["oblique"] += 1
            prefabs[name].append(
                ["p"] + [round(v, ROUND) for p in pts for v in p] + [kind])
    return prefabs, is_piece, tally


def envelope(parts) -> tuple | None:
    """The prefab's whole solid AABB in its own local frame, or None."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for part in parts:
        pts = ([(part[1], part[2], part[3]), (part[4], part[5], part[6])]
               if part[0] == "b" else
               [tuple(part[1 + 3 * i:4 + 3 * i]) for i in range(8)])
        for p in pts:
            for a in range(3):
                lo[a] = min(lo[a], p[a])
                hi[a] = max(hi[a], p[a])
    return None if lo[0] == float("inf") else (tuple(lo), tuple(hi))


def outer_of(a, b) -> str:
    """Which of two envelopes CONTAINS the other: 'a', 'b', 'same' or ''."""
    if a is None or b is None:
        return "b" if a is None else ("a" if b is None else "same")
    a_in_b = all(b[0][i] <= a[0][i] and a[1][i] <= b[1][i] for i in range(3))
    b_in_a = all(a[0][i] <= b[0][i] and b[1][i] <= a[1][i] for i in range(3))
    if a_in_b and b_in_a:
        return "same"
    if a_in_b:
        return "b"
    if b_in_a:
        return "a"
    return ""


def merge(base: dict, prefabs: dict, is_piece: dict, tally: dict,
          mods: str) -> tuple[dict, dict]:
    """ADD what the base corpus has never seen; change nothing it already has.

    WHY ADDITIVE AND NOT A REPLACEMENT.  Live verdicts are being measured
    THROUGH this file by other readers while it is rewritten - `structures`
    filters a body's pieces by `build_pieces`, `harbours` reads a hull's air
    draft out of `prefabs`.  A wholesale rewrite moves every one of those
    populations at once, so a before/after taken across it is not like-for-
    like even when nothing is wrong.  So every key the base already carries
    keeps its bytes UNLESS the new dump measured a LARGER SOLID for it.

    THE ONE RULE FOR A DISAGREEMENT, with no prefab list in it: A ROW IS
    RESOLVED TO WHICHEVER DUMP'S SOLID IS THE OUTER BOUND, because a
    clearance check that under-measures a solid is a check that cannot
    refuse.  "Keep the older value" is not the conservative choice, it is the
    STALE one: MEASURED here, `VikingShip`'s top is 10.9142 in a vanilla dump
    and 11.3846 under this world's own mod set, and the smaller number is a
    mast this deployment does not have - it would sail a hull under a 10.3 m
    lintel it hits.  Where NEITHER envelope contains the other the row is
    left at base and REPORTED, because a corpus must not invent a shape
    neither dump measured.

    Returns the merged document and a diff report.
    """
    old_pref = base["prefabs"]
    old_pieces = set(base.get("build_pieces", ()))
    added = sorted(set(prefabs) - set(old_pref))
    dropped = sorted(set(old_pref) - set(prefabs))
    changed = sorted(k for k in set(old_pref) & set(prefabs)
                     if json.dumps(old_pref[k], sort_keys=True)
                     != json.dumps(prefabs[k], sort_keys=True))
    new_pieces = sorted(k for k in added if is_piece.get(k))
    merged_pref = dict(old_pref)
    for k in added:
        merged_pref[k] = prefabs[k]
    widened, kept_at_base, incomparable = [], [], []
    for k in changed:
        which = outer_of(envelope(old_pref[k]), envelope(prefabs[k]))
        if which == "b":
            merged_pref[k] = prefabs[k]
            widened.append(k)
        elif which == "":
            incomparable.append(k)
        else:
            kept_at_base.append(k)
    doc = dict(base)
    doc["prefabs"] = merged_pref
    doc["build_pieces"] = sorted(old_pieces | set(new_pieces))
    doc["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc["merged"] = {
        "mode": ("additive; a disagreeing row is resolved to whichever dump's "
                 "solid is the OUTER BOUND, and left at base when neither "
                 "envelope contains the other"),
        "utc": doc["generated_utc"],
        "mods_staged": mods,
        "prefabs_added": len(added),
        "build_pieces_added": len(new_pieces),
        "prefabs_in_base_absent_from_new_dump": len(dropped),
        "prefabs_both_dumps_disagree_about": len(changed),
        "disagreeing_prefabs": changed[:200],
        "widened_to_the_new_dump": widened,
        "kept_at_base_new_dump_no_larger": kept_at_base,
        "kept_at_base_envelopes_incomparable": incomparable,
    }
    n_solid = sum(1 for k in added if merged_pref[k])
    rows = [row for k in added for row in prefabs[k]]
    t = dict(doc.get("totals") or {})
    t["prefabs"] = len(merged_pref)
    t["prefabs_without_solid"] = sum(1 for v in merged_pref.values() if not v)
    t["colliders"] = t.get("colliders", 0) + len(rows)
    t["axis_aligned"] = t.get("axis_aligned", 0) + sum(
        1 for r in rows if r[0] == "b")
    t["oblique"] = t.get("oblique", 0) + sum(1 for r in rows if r[0] == "p")
    t["bounds_only_colliders"] = t.get("bounds_only_colliders", 0) + sum(
        1 for r in rows if r[-1] == "mesh")
    t["tight_colliders"] = t.get("tight_colliders", 0) + sum(
        1 for r in rows if r[-1] != "mesh")
    doc["totals"] = t
    return doc, dict(added=added, dropped=dropped, changed=changed,
                     new_pieces=new_pieces, with_solid=n_solid)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tsv", help="raw dump from run_piecegeometry.sh")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--game-version", default="1.0.12")
    ap.add_argument("--merge-into", default=None,
                    help="an existing corpus to ADD to rather than replace; "
                         "every key it already carries keeps its bytes and "
                         "any row the new dump disagrees about is REPORTED "
                         "BY NAME instead of applied")
    ap.add_argument("--mods", default="",
                    help="the MODS value the dump was taken with, recorded in "
                         "the corpus so a later reader can tell 'this prefab "
                         "has no collider' from 'this prefab was never in the "
                         "scene'")
    ap.add_argument("--report", default=None,
                    help="write the full merge diff as JSON here")
    args = ap.parse_args(argv)

    src = Path(args.tsv)
    with src.open(newline="", encoding="utf-8") as fh:
        prefabs, is_piece, tally = compact(csv.DictReader(fh, delimiter="\t"))

    if args.merge_into:
        base = json.loads(Path(args.merge_into).read_text())
        doc, diff = merge(base, prefabs, is_piece, tally, args.mods)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, separators=(",", ":")) + "\n",
                       encoding="utf-8")
        if args.report:
            Path(args.report).write_text(json.dumps(diff, indent=1))
        print(f"{out}: MERGED. prefabs {len(base['prefabs'])} -> "
              f"{len(doc['prefabs'])} (+{len(diff['added'])}, "
              f"{diff['with_solid']} of them with a solid); build_pieces "
              f"{len(base.get('build_pieces', ()))} -> "
              f"{len(doc['build_pieces'])} (+{len(diff['new_pieces'])}); "
              f"{len(diff['changed'])} existing keys the two dumps disagree "
              f"about ({len(doc['merged']['widened_to_the_new_dump'])} "
              f"widened to the new dump's outer bound, "
              f"{len(doc['merged']['kept_at_base_new_dump_no_larger'])} kept "
              f"at base because base is already the outer bound, "
              f"{len(doc['merged']['kept_at_base_envelopes_incomparable'])} "
              f"kept at base as incomparable); "
              f"{len(diff['dropped'])} base keys absent from the new dump, "
              f"kept", file=sys.stderr)
        return 0

    doc = {
        "generated_by": "tools/jumpstart/blueprints/build_piece_geometry.py",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "tools/jumpstart/blueprints/PieceGeometry.cs via run_piecegeometry.sh",
        "game_version": args.game_version,
        "frame": "prefab root local space; a blueprint row's x/y/z is in this frame",
        "units": "metres",
        "solid_only": True,
        "totals": dict(tally, prefabs=len(prefabs),
                       prefabs_without_solid=sum(1 for v in prefabs.values() if not v)),
        "build_pieces": sorted(k for k, v in is_piece.items() if v),
        "prefabs": prefabs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{out}: {out.stat().st_size} bytes, {len(prefabs)} prefabs, "
          f"{tally['colliders']} solid colliders "
          f"({tally['axis_aligned']} axis-aligned, {tally['oblique']} oblique), "
          f"{doc['totals']['prefabs_without_solid']} prefabs with no solid collider",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
