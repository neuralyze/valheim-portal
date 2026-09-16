#!/usr/bin/env python3
"""Choose the bodies: which of the 528 hash-verified blueprints go where.

THREE HARD GATES, applied before any preference is expressed. A body that fails
one is not ranked lower, it is OUT:

1. GROUNDS. `flat_pad_verdict` must be `grounds` or `grounds_on_posts`.
   `DEFECT_on_land` is the operator's own complaint -- a building standing in
   the air over a flat pad -- and `expected_over_water` belongs to `Crossings`,
   not to a hillside. MEASURED by `library/audit_bases.py` from the game's own
   piece colliders: 89 of 538 rows are `DEFECT_on_land` and every one of them
   is refused here.
2. SPAWNS. `missing_prefabs` must be EMPTY. A body needing `--drop-prefab` is a
   worse choice and one needing several is disqualified, so the gate is zero
   rather than few.
3. FITS. The body's measured footprint must fit inside the pad the site can
   actually deliver, with a wall-to-pad-edge margin.

THEN THE NPC GATE, which is the one the operator's remark forces
------------------------------------------------------------------
"we might add npc/villager mod later" is a LAYOUT requirement, not a mod
requirement: a villager needs somewhere to stand INSIDE a building, and a body
whose interior no one can occupy is a stage set. So every town body is put
through `blueprints/fixtures.py::interior()`, which classifies the pad at 1 m
into OUTDOOR / INDOOR_COVERED / INDOOR_UNCOVERED / COVERED_UNENCLOSED /
UNREACHABLE / BLOCKED from the body's own geometry, with reachability computed
by flood fill from the rim rather than assumed. A town body must have at least
`MIN_INDOOR_M2` of INDOOR_COVERED cells, and those cells must be REACHABLE --
which `interior()` already guarantees, because an unreachable cell is
classified UNREACHABLE and not INDOOR_COVERED.

That check is not free (it rasterises every piece collider), so it runs on the
shortlist rather than on all 528.

WHAT IS DELIBERATELY NOT A GATE
-------------------------------
`relief_m` -- the peak-to-peak spread of the body's own base. A large relief
with no floating area is a FOUNDATION, not a defect: MEASURED,
`PuP_black_house_full` has 68.9 m of base relief and not one floating column.
The floating test is `flat_pad_verdict`, and it is already gate 1.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "blueprints"))

LIB_DATA = JUMPSTART / "library/data"

# TASTE PICK: how much bare pad is left between a body's measured footprint edge
# and the pad edge, so a building does not read as standing on a plinth exactly
# its own size.
PAD_MARGIN_M = 3.0
# A villager needs somewhere to be. Four square metres of covered, enclosed,
# reachable floor is the smallest space in which one could stand and turn
# around; below that a body is scenery. TASTE PICK on the threshold, MEASURED
# on the area.
MIN_INDOOR_M2 = 4.0

GROUNDS = ("grounds", "grounds_on_posts")


def load_audit() -> list[dict]:
    return json.loads((LIB_DATA / "base_audit.json").read_text())["entries"]


def load_manifest() -> dict[str, dict]:
    man = json.loads((LIB_DATA / "library_manifest.json").read_text())
    return {e["name"]: e for e in man["entries"]}


def eligible(rows: list[dict]) -> list[dict]:
    """Gates 1 and 2, applied to the whole library."""
    out = []
    for r in rows:
        if r.get("flat_pad_verdict") not in GROUNDS:
            continue
        if r.get("missing_prefabs"):
            continue
        if r.get("verdict") == "UNUSABLE":
            continue
        if not r.get("pieces"):
            continue
        out.append(r)
    return out


def fits(r: dict, pad_m: float, margin_m: float = PAD_MARGIN_M) -> bool:
    return (max(float(r["footprint_x_m"]), float(r["footprint_z_m"]))
            <= pad_m - 2 * margin_m)


def span(r: dict) -> float:
    return max(float(r["footprint_x_m"]), float(r["footprint_z_m"]))


# --- roles ----------------------------------------------------------------
# Each role is (predicate, sort key). The predicate is a HARD filter for that
# role; the sort key expresses preference among survivors. Keeping them apart
# means a role never silently accepts something it rules out.

def _tier_ok(r: dict, tiers: tuple[str, ...]) -> bool:
    return r.get("top_tier") in tiers


def role_pool(rows: list[dict], role: str, pad_m: float) -> list[dict]:
    e = [r for r in eligible(rows) if fits(r, pad_m)]
    if role == "town_hall":
        c = [r for r in e if 10 <= span(r) <= 30 and r["pieces"] >= 100
             and _tier_ok(r, ("meadows", "blackforest"))]
        c.sort(key=lambda r: (-r["walkable_levels"], -r["stations"], r["pieces"]))
    elif role == "town_house":
        c = [r for r in e if 4 <= span(r) <= 20 and 25 <= r["pieces"] <= 800
             and _tier_ok(r, ("meadows", "blackforest"))
             and r["category"] in ("base", "flavour", "production", "outpost")]
        c.sort(key=lambda r: (-min(r["beds"], 1), -min(r["stations"], 4), r["pieces"]))
    elif role == "town_workshop":
        c = [r for r in e if 4 <= span(r) <= 22 and r["stations"] >= 3
             and _tier_ok(r, ("meadows", "blackforest"))]
        c.sort(key=lambda r: (-r["stations"], r["pieces"]))
    elif role == "watchtower":
        c = [r for r in e if 6 <= span(r) <= 16 and r["walkable_levels"] >= 2
             and r["pieces"] <= 900
             and ("tower" in r["name"].lower() or r["category"] in ("defence", "outpost"))]
        c.sort(key=lambda r: (-r["walkable_levels"], r["floating_fraction"], r["pieces"]))
    elif role == "castle":
        c = [r for r in e if 24 <= span(r) <= pad_m - 2 * PAD_MARGIN_M
             and r["pieces"] >= 700
             and (r["category"] in ("base", "defence")
                  or any(k in r["name"].lower() for k in ("castle", "fort", "keep", "citadel")))]
        c.sort(key=lambda r: (-r["walkable_levels"], r["floating_fraction"], -r["pieces"]))
    elif role == "lighthouse":
        c = [r for r in e if 6 <= span(r) <= 18 and r["walkable_levels"] >= 6]
        c.sort(key=lambda r: (
            0 if any(k in r["name"].lower() for k in ("lighthouse", "beacon")) else 1,
            -r["walkable_levels"], r["floating_fraction"]))
    elif role == "treehouse":
        c = [r for r in e if 4 <= span(r) <= 26
             and ("stilt" in r["name"].lower() or "treehouse" in r["name"].lower()
                  or "tree_house" in r["name"].lower())]
        c.sort(key=lambda r: (0 if "treehouse" in r["name"].lower() else 1,
                              -r["walkable_levels"], -r["pieces"]))
    elif role == "town_portal":
        c = [r for r in e if span(r) <= 14 and r["portals"] >= 1 and r["pieces"] <= 400]
        c.sort(key=lambda r: (r["pieces"],))
    else:
        raise ValueError(f"unknown role {role}")
    return c


# --- the NPC gate ---------------------------------------------------------

def interior_report(name: str, yaw_deg: float = 0.0) -> dict:
    """Rasterise one body and report its interior, via fixtures.py.

    Returns the mask summary plus the pass/fail against MIN_INDOOR_M2. Imports
    are done here rather than at module scope because they pull in the piece
    geometry table, which is expensive and not needed to rank a pool.

    `library/materialise.py` and `blueprints/materialise.py` are DIFFERENT
    modules with the same name, and only the library one carries
    `locate_by_filename` -- the single hash-verified resolver, which refuses an
    ambiguous source name rather than guessing (MEASURED: `PuP_Minicastle.blueprint`
    is two different castles, and a guessing resolver once made a survey
    measure a 3,334-piece body while the manifest claimed 3,810). It is loaded
    by path here so import order cannot decide which one answers.
    """
    import importlib.util
    import fixtures
    from to_rcon_plan import read_objects

    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    libmat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(libmat)

    path = libmat.locate_by_filename(name)
    if path is None:
        raise FileNotFoundError(f"no body resolves for {name}")
    objects = read_objects(path)
    body = fixtures.place_body(objects, (0.0, 0.0, 0.0), yaw_deg=yaw_deg)
    mask = fixtures.interior(body)
    s = mask.summary()
    indoor = s.get("indoor_covered_area_m2", 0.0)
    return {
        "name": name, "yaw_deg": yaw_deg, "pieces": len(objects),
        "indoor_covered_m2": indoor,
        "counts": s.get("counts", {}),
        "cover_height_m": s.get("cover_height_m"),
        "interior_floor_above_pad_m": s.get("interior_floor_above_pad_m"),
        "npc_ok": indoor >= MIN_INDOOR_M2,
        "npc_verdict": (
            f"MEASURED {indoor:.1f} m2 of covered, enclosed, reachable floor "
            f"(>= {MIN_INDOOR_M2:g} m2): a villager can stand inside"
            if indoor >= MIN_INDOOR_M2 else
            f"MEASURED only {indoor:.1f} m2 of covered, enclosed, reachable floor "
            f"(< {MIN_INDOOR_M2:g} m2): nothing could stand in this body, it is scenery"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="town_house")
    ap.add_argument("--pad", type=float, default=24.0)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--interior", action="store_true",
                    help="run the fixtures interior mask on the shortlist (slow)")
    a = ap.parse_args()
    rows = load_audit()
    pool = role_pool(rows, a.role, a.pad)
    print(f"# role {a.role} pad {a.pad} m: {len(pool)} of {len(rows)} rows survive the gates")
    for r in pool[:a.top]:
        print(f"  {r['name'][:54]:56s} {r['category']:10s} "
              f"{r['footprint_x_m']:5.1f}x{r['footprint_z_m']:5.1f} pcs={r['pieces']:5d} "
              f"by={r['base_y']:6.2f} {r['flat_pad_verdict']:16s} "
              f"float={r['floating_fraction']:.2f} st={r['stations']:4d} bed={r['beds']} "
              f"lvl={r['walkable_levels']:3d} tier={r['top_tier']}")
    if a.interior:
        print("\n# interior mask (the NPC gate)")
        for r in pool[:a.top]:
            try:
                rep = interior_report(r["name"])
            except Exception as e:  # a body we cannot rasterise is not a candidate
                print(f"  {r['name'][:54]:56s} RASTER FAILED {type(e).__name__}: {e}")
                continue
            print(f"  {rep['name'][:54]:56s} indoor={rep['indoor_covered_m2']:7.1f} m2 "
                  f"{'PASS' if rep['npc_ok'] else 'FAIL'} counts={rep['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
