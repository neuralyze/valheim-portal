#!/usr/bin/env python3
"""Re-cost a placement's earthwork after its BODY changed, without re-solving the site.

Why this exists. `blueprints/solve_placements.py` picks a site AND costs the pad
in one pass, so `solved.flatten_cost` is only true for the body that was in the
placement when it ran. Swapping the body is exactly what body selection does, and
two of the numbers it invalidates are load-bearing:

  * `solved.footprint_orientation` is what `terraform/flatten.py::pad_extent`
    reads to decide WHICH RECTANGLE gets levelled. Leave it stale and the pad is
    cut to the old body's shape.
  * `solved.flatten_cost.target_y` is the PAD HEIGHT, and everything after it --
    `to_rcon_plan.py --align floor-center`, every fixture in `terraform/stock.py`
    -- is expressed relative to it. It is the median generated height over that
    rectangle, so a different rectangle is a different median.

Re-solving would answer both, and would also move the site. One site on this
world is already BUILT (`pre-bonemass/iron-era-workshop`, 917 pieces in the
ground), so a pass that may relocate sites is not an option. This re-costs at the
site the solver already chose, from the same height source the solver used.

MEASUREMENT, not estimate. Heights come from PatchScan
(`blueprints/run_patchscan.sh`) at step 1 with rivers included, which is the only
height source on this host that agrees with the game: MEASURED and recorded in
`network/README.md`, the 8 m river-free seedscan grid reads 48.29 m at
(-308, 172) where the 1 m river-inclusive sample reads 35.43 m.

THE CLAMP IS A VERDICT. `TerrainComp::ApplyToHeightmap` clamps a per-sample
delta to +/-8 m (`terraform/tcdata.CLAMP_M`), so a pad needing more than that
CANNOT be cut, and no choice of body fixes it -- it is the ground. Samples over
the clamp are reported and `--write` refuses.

Usage:
    network/recost_pad.py --preset pre-queen --id mistlands-blackforge-base
    network/recost_pad.py --preset pre-queen --id mistlands-blackforge-base --write
    network/recost_pad.py --preset pre-bonemass --id iron-era-workshop   # self-check
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "library"))

import tcdata  # noqa: E402
import flatten as flattenmod  # noqa: E402

# Valheim's water plane, in WorldGenerator.GetHeight units. Imported rather than
# restated: a 0 here is the mistake that put 10 of 13 placements under the sea.
sys.path.insert(0, str(JUMPSTART / "blueprints"))
from site_finder import WATER_LEVEL  # noqa: E402

MANIFEST = JUMPSTART / "library" / "data" / "library_manifest.json"


def manifest_rows() -> dict[str, dict]:
    doc = json.loads(MANIFEST.read_text("utf-8"))
    return {r["name"]: r for r in doc["entries"]}


def body_rect(place: dict, rows: dict[str, dict]) -> tuple[float, float, str]:
    """The rectangle to level, and where that shape came from.

    A placement that declares `requirement.footprint_xz_m` is asking for the
    BODY's own rectangle -- that is how the four rectangle-padded sites on this
    world are written. A placement that does not is asking for the square
    `requirement.footprint_m`, and MEASURED across this tree six of the nine
    presets are written that way, which is why most body swaps do not move the
    pad at all.
    """
    req = place["requirement"]
    if "footprint_xz_m" in req:
        row = rows[place["blueprint"]]
        fx, fz, _fy = (float(v) for v in row["footprint"].split("x"))
        return fx, fz, "body rectangle (requirement declares footprint_xz_m)"
    side = float(req["footprint_m"])
    return side, side, "square requirement.footprint_m"


def cost(patches, cx: float, cz: float, w: float, d: float,
         target_override: float | None = None) -> dict:
    """Cut/fill over the rectangle at 1 m, against the target.

    The default target is the MEDIAN generated height, because the pad is
    levelled to one height and the median is the height that minimises total
    metres moved. That is the choice `solve_placements.py` made, and the
    self-check on an unchanged placement is what proves the two agree: MEASURED
    on `pre-bonemass/iron-era-workshop`, the median over the same 70 x 70 m
    rectangle reproduces the solver's `target_y` of 70.91 exactly.

    `target_override` exists for the one case the median gets wrong: a pad whose
    median lands under the y=30 water plane. Cheapest cut is the wrong objective
    when the result is a floor below sea level, so a caller may declare the
    height and the basis is recorded with it.
    """
    half_w, half_d = w / 2, d / 2
    samples: list[tuple[float, float, float]] = []
    for patch in patches.values():
        for i in range(patch.n):
            for j in range(patch.n):
                wx, wz = patch.world(i, j)
                if abs(wx - cx) > half_w or abs(wz - cz) > half_d:
                    continue
                samples.append((wx, wz, patch.at(i, j)))
    if not samples:
        raise SystemExit("no samples inside the rectangle -- wrong zones?")
    # Edge samples belong to two zones, so de-duplicate on the coordinate.
    uniq: dict[tuple[float, float], float] = {}
    for wx, wz, h in samples:
        uniq[(round(wx, 3), round(wz, 3))] = h
    hs = list(uniq.values())
    target = statistics.median(hs) if target_override is None else float(target_override)
    cut = sum(h - target for h in hs if h > target)
    fill = sum(target - h for h in hs if h < target)
    deltas = [target - h for h in hs]
    over = sum(1 for dv in deltas if abs(dv) > tcdata.CLAMP_M)
    return {
        "target_y": round(target, 2),
        "cut_m3": round(cut, 1),
        "fill_m3": round(fill, 1),
        "moved_m3": round(cut + fill, 1),
        "mean_move_m": round((cut + fill) / len(hs), 3),
        "rms_m": round(math.sqrt(sum(dv * dv for dv in deltas) / len(hs)), 3),
        "max_cut_m": round(max((h - target for h in hs), default=0.0), 2),
        "max_fill_m": round(max((target - h for h in hs), default=0.0), 2),
        "resolution_m": 1.0,
        "estimated": False,
        "samples": len(hs),
        "over_clamp_samples": over,
        "min_generated_m": round(min(hs), 2),
        "max_generated_m": round(max(hs), 2),
        "min_freeboard_m": round(min(hs) - WATER_LEVEL, 2),
        "target_freeboard_m": round(target - WATER_LEVEL, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--seed", default="Pirate68")
    ap.add_argument("--write", action="store_true",
                    help="update solved.flatten_cost, footprint_orientation and "
                         "requirement.footprint_xz_m in place")
    ap.add_argument("--target-y", type=float,
                    help="declare the pad height instead of taking the median; REQUIRES "
                         "--target-basis so the choice travels with the number")
    ap.add_argument("--target-basis",
                    help="why this pad height rather than the median")
    args = ap.parse_args()

    path = JUMPSTART / "worlds" / args.world / args.preset / "placements.yaml"
    doc = yaml.safe_load(path.read_text())
    place = next((p for p in doc["placements"] if p["id"] == args.id), None)
    if place is None:
        raise SystemExit(f"no placement {args.id} in {path}")
    solved = place["solved"]
    cx, cz = float(solved["x"]), float(solved["z"])
    rows = manifest_rows()
    w, d, why = body_rect(place, rows)

    zones = flattenmod.zones_for(cx, cz, w, d)
    out = Path("/tmp/terraform") / f"recost_{args.preset}_{args.id}.bin"
    patches = flattenmod.run_patchscan(sorted(zones), args.seed, out)
    if (args.target_y is None) != (args.target_basis is None):
        raise SystemExit("--target-y and --target-basis go together: a declared height without a "
                         "written reason is the kind of number nobody can check later")
    new = cost(patches, cx, cz, w, d, args.target_y)

    old = solved.get("flatten_cost") or {}
    print(f"{args.preset}#{args.id}  body {place['blueprint']}")
    print(f"  rectangle {w:.1f} x {d:.1f} m from {why}; zones {sorted(zones)}")
    print(f"  {'':14s} {'OLD':>12s} {'NEW':>12s}")
    for key in ("target_y", "moved_m3", "mean_move_m", "max_cut_m", "max_fill_m"):
        print(f"  {key:14s} {str(old.get(key, '-')):>12s} {str(new[key]):>12s}")
    print(f"  samples {new['samples']}, generated {new['min_generated_m']}..{new['max_generated_m']} m, "
          f"min freeboard {new['min_freeboard_m']} m, target freeboard {new['target_freeboard_m']} m")
    print(f"  over the +/-{tcdata.CLAMP_M:g} m apply clamp: {new['over_clamp_samples']} samples")
    # A pad levelled below y=30 puts the building's floor under water. The
    # median is the cheapest cut, not a correct one, so this is reported as a
    # refusal that a declared --target-y answers.
    if new["target_freeboard_m"] < 0 and args.target_y is None:
        print(f"  REFUSED: the median pad height {new['target_y']} is {-new['target_freeboard_m']:.2f} m "
              f"BELOW the y={WATER_LEVEL:g} water plane, so the building's floor would be under "
              f"water. Declare a height with --target-y/--target-basis.", file=sys.stderr)
        return 3

    if new["over_clamp_samples"]:
        print("  REFUSED: this pad cannot be cut. TerrainComp::ApplyToHeightmap clamps "
              "each sample to +/-8 m, so the levelled result would not match target_y.",
              file=sys.stderr)
        return 2
    if not args.write:
        return 0

    solved["flatten_cost"] = {k: v for k, v in new.items()
                              if k not in ("samples", "over_clamp_samples",
                                           "min_generated_m", "max_generated_m",
                                           "min_freeboard_m", "target_freeboard_m")}
    solved["footprint_orientation"] = f"{w:.1f} m along x by {d:.1f} m along z"
    solved["recost"] = {
        "by": "network/recost_pad.py",
        "reason": "body changed; flatten_cost and footprint_orientation belong to the body's "
                  "rectangle, not the previous body's",
        "rectangle_source": why,
        "samples": new["samples"],
        "generated_range_m": [new["min_generated_m"], new["max_generated_m"]],
        "min_freeboard_m": new["min_freeboard_m"],
        "target_freeboard_m": new["target_freeboard_m"],
        "over_clamp_samples": 0,
        "height_source": "blueprints/run_patchscan.sh, step 1 m, rivers included",
    }
    if args.target_y is not None:
        solved["recost"]["target_y_declared"] = args.target_y
        solved["recost"]["target_y_median_would_be"] = round(
            cost(patches, cx, cz, w, d)["target_y"], 2)
        solved["recost"]["target_y_basis"] = args.target_basis
    if "footprint_xz_m" in place["requirement"]:
        place["requirement"]["footprint_xz_m"] = [round(w, 1), round(d, 1)]
    path.write_text(yaml.safe_dump(doc, default_flow_style=False, width=100, sort_keys=False))
    print(f"  written -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
