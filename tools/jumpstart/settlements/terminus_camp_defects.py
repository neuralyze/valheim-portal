#!/usr/bin/env python3
"""Run every check in `terminus_camp.py` AGAINST THE DEFECT IT EXISTS TO CATCH.

A version of a check that has only ever passed is indistinguishable from the
broken one it replaced.  So each case here feeds the real check a deliberately
wrong world and asserts that it FAILS; the harness itself fails if any check
comes back clean on a defect.

Case 1 is the defect that produced this ticket, replayed exactly: the road's
last standing lamp, 26.65 m from the arrival point, against a 15.0 m point
light.  The sightline metric that reported the terminus as lit had an 80 m cap
and returned TRUE for it.  `fires()` must return FALSE.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import terminus_camp as TC  # noqa: E402

CASES: list[dict] = []


def case(name: str, defect: str, instrument: str):
    def deco(fn):
        CASES.append({"name": name, "defect": defect, "instrument": instrument,
                      "fn": fn})
        return fn
    return deco


# ---------------------------------------------------------------------------

@case("the ticket's own defect: a flame in view but out of range",
      "the last road lamp at (-872.41, 34.996, 7733.55) is 26.65 m from the "
      "arrival point; piece_groundtorch's Light is a POINT light of range "
      "15.0 m. A sightline at an 80 m cap reported this as lit.",
      "fires()")
def _c1(ap, patches, ps, rep):
    lamp = [{"prefab": "piece_groundtorch", "x": -872.41,
             "y": 34.996 + TC.GROUNDTORCH_SEAT_OFF, "z": 7733.55,
             "yaw_deg": 0.0, "role": "road_lighting", "why": "the real lamp"}]
    got = TC.fires(ap, patches, lamp)
    f = got["flames"][0]
    return {"fails_as_required": not f["illuminates_arrival"],
            "distance_to_arrival_m": f["distance_to_arrival_m"],
            "range_m": f["range_m"],
            "shortfall_m": round(f["distance_to_arrival_m"] - f["range_m"], 3),
            "note": "26.65 m measured against a 15.0 m range: the check says "
                    "NOT illuminated, which is the answer the sightline "
                    "metric could not give."}


@case("a fire capped by its own roof emits nothing",
      "Fireplace.CheckUnderTerrain raycasts 0.5 m up from pos + "
      "up*m_coverCheckOffset; any solid hit sets m_blocked, and IsBurning() "
      "returns false at its first line when m_blocked.",
      "fires().blocked_by_cover_ray")
def _c2(ap, patches, ps, rep):
    bad = copy.deepcopy(ps)
    fire = next(p for p in bad if p["prefab"] == "fire_pit")
    # A ceiling slab 0.7 m over the fire: inside the 0.50..1.00 m ray window.
    bad.append({"prefab": "wood_floor", "x": fire["x"],
                "y": fire["y"] + 0.70, "z": fire["z"], "yaw_deg": 0.0,
                "role": "site_body", "why": "the defect: a ceiling too low"})
    got = TC.fires(ap, patches, bad)
    f = next(x for x in got["flames"] if x["prefab"] == "fire_pit")
    return {"fails_as_required": f["blocked_by_cover_ray"],
            "cover_ray_hits": f["cover_ray_hits"],
            "note": "the fire is 4.717 m from the arrival point and inside "
                    "its 10.0 m range, and it still emits nothing -- which is "
                    "why distance alone is not the whole check."}


@case("a buried fire emits nothing",
      "Fireplace.CheckUnderTerrain sets m_blocked when "
      "Heightmap.GetHeight(pos) > pos.y + m_checkTerrainOffset; the offset is "
      "0.20 m for fire_pit.",
      "fires().blocked_by_terrain")
def _c3(ap, patches, ps, rep):
    bad = copy.deepcopy(ps)
    fire = next(p for p in bad if p["prefab"] == "fire_pit")
    fire["y"] = TC.ground_at(ap, patches, fire["x"], fire["z"]) - 1.0
    got = TC.fires(ap, patches, bad)
    f = next(x for x in got["flames"] if x["prefab"] == "fire_pit")
    return {"fails_as_required": f["blocked_by_terrain"],
            "buried_beyond_offset_m": f["buried_beyond_offset_m"],
            "note": "1.0 m under the ground, 0.20 m of tolerance."}


@case("a torch seated at the surface floats",
      "piece_groundtorch's pivot is 0.65359 m UP THE SHAFT (mesh and collider "
      "both span -0.65359..0.82260), so seating it as though the pivot were "
      "its foot leaves it hanging.",
      "post_reach()/seat arithmetic")
def _c4(ap, patches, ps, rep):
    naive = [{"prefab": "piece_groundtorch", "x": t[0],
              "y": TC.TORCH_SURFACE_Y, "z": t[1], "yaw_deg": 0.0,
              "role": "site_prop", "why": "the defect: pivot treated as foot"}
             for t in TC.TORCHES]
    float_m = [round(p["y"] + TC.GEOM["piece_groundtorch"][1]
                     - TC.ground_at(ap, patches, p["x"], p["z"]), 5)
               for p in naive]
    correct = [round(p["y"] + TC.GEOM["piece_groundtorch"][1]
                     - TC.ground_at(ap, patches, p["x"], p["z"]), 5)
               for p in ps if p["prefab"] == "piece_groundtorch"]
    return {"fails_as_required": all(abs(f + 0.65359) < 1e-4 for f in float_m),
            "naive_seat_foot_offset_m": float_m,
            "module_seat_foot_offset_m": correct,
            "note": "the naive seat puts the foot 0.65359 m BELOW ground (the "
                    "shaft buried, flame at ground level); the module's seat "
                    "puts it at 0.0."}


@case("posts that stop short",
      "wood_floor and wood_pole HAVE WearNTear, so a deck tile with nothing "
      "under it falls. harbour_city::piles sizes a stack to REACH and never "
      "to stop short for this reason.",
      "post_reach()")
def _c5(ap, patches, ps, rep):
    bad = [p for p in ps
           if not (p["prefab"] == "wood_pole"
                   and abs(p["x"] - (TC.CX + TC.BODY_W / 2)) < 1e-6
                   and abs(p["z"] - (TC.CZ + TC.BODY_D / 2)) < 1e-6)]
    got = TC.post_reach(ap, patches, bad)
    return {"fails_as_required": got["stopping_short"] > 0,
            "stopping_short": got["stopping_short"],
            "short_columns": got["short_columns"],
            "note": "the whole 3-pole stack removed from the deepest corner; "
                    "the check names that column and its shortfall."}


@case("a piece inside the arch's AABB",
      "portal_wood's origin IS its foot (AABB y -0.007..3.2868) and it stands "
      "at x -845.942..-844.763, z 7727.898..7732.132. PortalTriage's hazard: "
      "building there intersects the arch.",
      "arch_guard()")
def _c6(ap, patches, ps, rep):
    bad = copy.deepcopy(ps)
    bad.append({"prefab": "woodwall", "x": -845.4, "y": 34.5, "z": 7730.0,
                "yaw_deg": 0.0, "role": "site_body",
                "why": "the defect: a wall in the arch"})
    got = TC.arch_guard(bad)
    return {"fails_as_required": bool(got["pieces_inside_arch_aabb"]),
            "pieces_inside_arch_aabb": got["pieces_inside_arch_aabb"],
            "closest_approach_m": got["closest_approach_m"]}


@case("a natural object intersecting the body",
      "a boulder inside the footprint would have to be cleared, and a "
      "clearing is the only path by which the arch could be deleted.",
      "clashes()")
def _c7(ap, patches, ps, rep):
    floor = next(p for p in ps if p["prefab"] == "wood_floor")
    planted = [("Rock_3", floor["x"], floor["y"] - 0.2, floor["z"])]
    got = TC.clashes(ps, planted)
    real = TC.clashes(ps, TC.LIVE_NATURALS)
    return {"fails_as_required": bool(got),
            "planted_clashes": len(got), "live_clashes": len(real),
            "note": "a boulder placed in the deck is detected; the six real "
                    "ones are not, because none of them intersects -- which "
                    "is why this build removes nothing."}


@case("a census that rasterises a rotated piece with unrotated extents",
      "`woodwall` is 2.00 m along its OWN x and 0.30 m thick. Rasterised "
      "without its yaw, the two panels standing at yaw 90 are turned through "
      "90 degrees on the raster, inventing columns beyond the body's own "
      "edge. Same family as the y-blind box census that produced 64 "
      "duplicate pieces.",
      "world_box() vs the unrotated box")
def _c9(ap, patches, ps, rep):
    wall = next(p for p in ps
                if p["prefab"] == "woodwall" and round(p["yaw_deg"]) == 90)
    g = TC.geom_of("woodwall")
    naive = (wall["x"] + g[0], wall["x"] + g[3],
             wall["z"] + g[2], wall["z"] + g[5])
    bx0, _, bz0, bx1, _u, bz1 = TC.world_box(wall)
    east = TC.CX + TC.BODY_W / 2
    # The naive box reaches past the body's own east edge; the rotated one
    # does not. That overhang is what invented the phantom columns.
    return {"fails_as_required": naive[1] > east + 0.2 and bx1 <= east + 0.2,
            "wall_at": [wall["x"], wall["z"], wall["yaw_deg"]],
            "body_east_edge_x": east,
            "unrotated_x_span": [round(naive[0], 3), round(naive[1], 3)],
            "rotated_x_span": [round(bx0, 3), round(bx1, 3)],
            "phantom_overhang_m": round(naive[1] - east, 3),
            "note": "the unrotated raster put this 0.30 m-thick panel 1.0 m "
                    "east of the body and the census then reported two "
                    "columns floating 0.211 and 1.347 m over ground that "
                    "carries nothing."}


@case("a roof whose eaves are set from the ridge instead of the walls",
      "THE DEFECT THAT REACHED THE LIVE WORLD. The first build put each "
      "wood_roof pivot 1.0 m from the ridge instead of half the body, so all "
      "six panels crossed AT the ridge: the roof covered z 7731.5..7733.5 of "
      "a 7730.5..7734.5 body, both eaves were open to the sky, and there was "
      "a 1 m gap between wall-top and roof. Grounding, post reach, "
      "per-prefab count at tolerance 0, the arch guard and illumination ALL "
      "passed, because none of them asks what is overhead.",
      "cover()")
def _c10(ap, patches, ps, rep):
    bad = [p for p in ps if p["prefab"] != "wood_roof"]
    for p in ps:
        if p["prefab"] != "wood_roof":
            continue
        # Reconstruct the AS-BUILT (wrong) pivot: 1.0 m from the ridge.
        q = dict(p)
        q["z"] = (TC.RIDGE_Z + 1.0 - TC.ROOF_LOW_Z
                  if round(p["yaw_deg"]) == 0
                  else TC.RIDGE_Z - 1.0 + TC.ROOF_LOW_Z)
        bad.append(q)
    was = TC.cover(bad)
    now = TC.cover(ps)
    return {"fails_as_required": (not was["fully_roofed"]
                                  and now["fully_roofed"]),
            "as_built_open_to_sky_cells": was["open_to_sky"],
            "as_built_covered_fraction": was["covered_fraction"],
            "as_built_bed": was["bed"],
            "corrected_open_to_sky_cells": now["open_to_sky"],
            "corrected_covered_fraction": now["covered_fraction"],
            "corrected_bed": now["bed"],
            "note": "the bed happened to be under the crossed panels, so a "
                    "check that only asked about the bed would have passed "
                    "too; the deck-wide sweep is what names the open eaves."}


@case("the applied-surface instrument returning a false zero",
      "reading the DELTA instead of the APPLIED surface, or an instrument "
      "that answers 0 for everything, both look like 'no terrain here'.",
      "bootstrap()")
def _c8(ap, patches, ps, rep):
    class Dead:
        def surface_at(self, _p, _x, _z):
            return (0.0, 0.0, 0.0)
    got = TC.bootstrap(Dead(), patches)
    return {"fails_as_required": not got["both_directions_ok"],
            "dead_instrument_verdict": got["both_directions_ok"],
            "live_instrument_verdict": rep["bootstrap"]["both_directions_ok"],
            "note": "the ABSENT half of the bootstrap passes for a dead "
                    "instrument; only the PRESENT half catches it, which is "
                    "why both directions are required."}


def run() -> int:
    ap, patches = TC.surface()
    ps = TC.pieces(ap, patches)
    rep = TC.report(ap, patches)
    out, bad = [], 0
    for c in CASES:
        got = c["fn"](ap, patches, ps, rep)
        ok = bool(got.get("fails_as_required"))
        if not ok:
            bad += 1
        out.append({"case": c["name"], "defect": c["defect"],
                    "instrument": c["instrument"],
                    "check_fired": ok, **{k: v for k, v in got.items()
                                          if k != "fails_as_required"}})
    print(json.dumps({"cases": out, "total": len(CASES),
                      "checks_that_fired": len(CASES) - bad,
                      "checks_that_did_NOT_fire": bad,
                      "verdict": "every check fires on its defect" if not bad
                                 else "A CHECK DID NOT FIRE -- it is not a check"},
                     indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(run())
