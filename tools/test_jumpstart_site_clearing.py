#!/usr/bin/env python3
"""Tests for the site-clearing step's footprint-to-clearing-area derivation.

The defect these pin down, MEASURED in game by the operator on Ulfsland on
2026-09-15: the terrain under `iron-era-workshop` was flattened correctly and
the loose prefabs sat on the ground correctly, but "there were still trees
existing in the way".  Clearing was absent from the pipeline.

Two things then have to be right, and both are the kind of thing that answers
confidently while measuring the wrong quantity:

  * THE SHAPE.  The only headless removal primitive selects objects by
    `Utils.DistanceXZ` against a radius (MEASURED in UpgradeWorld.dll IL), so
    the cleared region is a vertical CYLINDER while the pad is a RECTANGLE.
    The radius has to be the pad's HALF-DIAGONAL; half-side is the plausible
    wrong answer and leaves standing forest in all four corners -- 14.5 m of
    it on the 70 x 70 m workshop pad.
  * THE ROTATION.  A placement carries a yaw.  The clearing radius must cover
    the pad at that yaw, and the circumscribed circle of a rectangle happens
    to be yaw-invariant -- but that is a property to be checked, not assumed,
    and it is only true because the radius is the half-diagonal.

The rest of the step (which RCON commands, in which order) is proven against
a throwaway server rather than here: a unit test cannot tell you that
`objects_remove` answers "Error: Missing ids." when given only `ignore=`.
"""

import math
import sys
import unittest
from pathlib import Path

import yaml

CLEARING = Path(__file__).resolve().parent / "jumpstart" / "clearing"
sys.path.insert(0, str(CLEARING))

import area as CA  # noqa: E402
import clear as CL  # noqa: E402


def place(**over) -> dict:
    """A solved placement shaped like the real ones in
    worlds/Ulfsland/pre-bonemass/placements.yaml."""
    doc = {
        "id": "iron-era-workshop",
        "footprint_xzy_m": [65.5, 66.0, 16.8],
        "requirement": {"footprint_m": 70, "location_clearance_m": 60},
        "rotation": {"units": "euler_degrees_yxz", "yaw": 180},
        "solved": {
            "x": -4673.5, "y": 75.23, "z": -310.5, "y_centre_m": 70.86,
            "footprint_orientation": "70.0 m along x by 70.0 m along z",
            "nearest_location_m": 113.0,
            "flatten_cost": {"target_y": 70.91, "max_cut_m": 4.32, "max_fill_m": 4.05},
        },
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            doc[key] = {**doc[key], **value}
        else:
            doc[key] = value
    return doc


def corners(w: float, d: float, yaw: float) -> list[tuple[float, float]]:
    """The pad's corners relative to its centre, spun by yaw."""
    rad = math.radians(yaw)
    cos, sin = math.cos(rad), math.sin(rad)
    out = []
    for sx in (-1, 1):
        for sz in (-1, 1):
            x, z = sx * w / 2, sz * d / 2
            out.append((x * cos + z * sin, -x * sin + z * cos))
    return out


class ClearingRadius(unittest.TestCase):
    def test_radius_is_the_half_diagonal_not_the_half_side(self):
        got = CA.clearing_area(place(), margin=0.0)
        self.assertAlmostEqual(got.radius_m, math.hypot(70.0, 70.0) / 2, places=6)
        self.assertGreater(got.radius_m, 35.0 + 14.0,
                           "half-side would leave 14.5 m of forest in every corner")

    def test_every_pad_corner_is_inside_the_cleared_cylinder_at_any_yaw(self):
        for yaw in (0, 37, 90, 180, 213.7, 270, 359.9):
            got = CA.clearing_area(place(rotation={"yaw": yaw}), margin=0.0)
            for cx, cz in corners(got.pad_w_m, got.pad_d_m, yaw):
                self.assertLessEqual(math.hypot(cx, cz), got.radius_m + 1e-9,
                                     f"corner ({cx:.2f}, {cz:.2f}) uncleared at yaw {yaw}")
            self.assertTrue(got.covers_rotated_pad())

    def test_a_half_side_radius_would_miss_the_corners(self):
        """The bug this derivation exists to prevent, stated as an assertion so
        the numbers cannot drift back to it unnoticed."""
        got = CA.clearing_area(place(), margin=0.0)
        half_side = max(got.pad_w_m, got.pad_d_m) / 2
        worst = max(math.hypot(cx, cz) for cx, cz in
                    corners(got.pad_w_m, got.pad_d_m, got.yaw_deg))
        self.assertGreater(worst, half_side)
        self.assertAlmostEqual(worst - half_side, 14.497, places=2)

    def test_rotation_does_not_change_the_radius_for_an_oblong_pad(self):
        """An 80 x 40 m pad is the case where a half-side radius is not even
        well defined.  The half-diagonal is the same at every yaw, and covers
        the corners at every yaw."""
        oblong = {"footprint_orientation": "80.0 m along x by 40.0 m along z"}
        radii = set()
        for yaw in (0, 45, 90, 137, 180, 313):
            got = CA.clearing_area(place(solved=oblong, rotation={"yaw": yaw}), margin=0.0)
            radii.add(round(got.radius_m, 9))
            for cx, cz in corners(got.pad_w_m, got.pad_d_m, yaw):
                self.assertLessEqual(math.hypot(cx, cz), got.radius_m + 1e-9)
        self.assertEqual(radii, {round(math.hypot(80.0, 40.0) / 2, 9)},
                         f"radius drifted with yaw: {radii}")

    def test_apron_and_margin_both_widen_the_area(self):
        base = CA.clearing_area(place(), margin=0.0)
        aproned = CA.clearing_area(place(), apron=5.0, margin=0.0)
        self.assertAlmostEqual(aproned.radius_m, math.hypot(80.0, 80.0) / 2, places=6)
        self.assertGreater(aproned.radius_m, base.radius_m)
        self.assertAlmostEqual(
            CA.clearing_area(place(), margin=2.0).radius_m - base.radius_m, 2.0, places=6)
        for cx, cz in corners(aproned.pad_w_m + 10, aproned.pad_d_m + 10, aproned.yaw_deg):
            self.assertLessEqual(math.hypot(cx, cz), aproned.radius_m + 1e-9)

    def test_pad_extent_comes_from_the_solver_not_the_blueprint_bounds(self):
        """`footprint_xzy_m` is the blueprint's own 65.5 x 66.0 m bounding box;
        the pad the flatten step levels is the solver's 70 x 70 m
        `footprint_orientation`.  Clearing has to match the flattened ground,
        so the larger, authoritative number is the one used."""
        got = CA.clearing_area(place(), margin=0.0)
        self.assertEqual((got.pad_w_m, got.pad_d_m), (70.0, 70.0))


class VerificationBox(unittest.TestCase):
    def test_verification_box_is_wholly_inside_the_cleared_cylinder(self):
        got = CA.clearing_area(place())
        half = got.verify_half_side_m
        self.assertLessEqual(half * math.sqrt(2), got.radius_m,
                             "a box corner outside the cylinder would report a false miss")
        self.assertGreater(half, 0.0)

    def test_verification_height_window_cannot_hide_a_tree_on_the_pads_own_slope(self):
        """`findObjects -near x y z r` is a CUBE: the same r bounds height.  The
        window has to clear the solver's worst cut/fill plus a standing tree."""
        got = CA.clearing_area(place())
        self.assertAlmostEqual(got.relief_m, 4.32, places=2)
        self.assertGreater(got.verify_relief_headroom_m(got.relief_m), 0.0)

    def test_headroom_goes_negative_when_the_pad_relief_swallows_the_window(self):
        got = CA.clearing_area(place())
        self.assertLess(got.verify_relief_headroom_m(200.0), 0.0)


class Refusals(unittest.TestCase):
    def test_refuses_when_clearing_would_reach_a_generated_location(self):
        near = place(solved={"nearest_location_m": 40.0})
        with self.assertRaises(CA.ClearAreaRefused) as caught:
            CA.clearing_area(near)
        self.assertIn("40.0", str(caught.exception))

    def test_bound_prefers_the_measured_distance_over_the_requirement(self):
        bound, source = CA.location_bound(place(solved={"nearest_location_m": 12.0}))
        self.assertEqual((bound, source), (12.0, "solved.nearest_location_m"))
        bound, source = CA.location_bound(
            place(requirement={"location_clearance_m": 8.0}))
        self.assertEqual((bound, source), (8.0, "requirement.location_clearance_m"))

    def test_refuses_an_unsolved_placement_instead_of_clearing_the_wrong_ground(self):
        unsolved = place()
        del unsolved["solved"]
        with self.assertRaises(CA.ClearAreaRefused):
            CA.clearing_area(unsolved)

    def test_clears_at_the_levelled_surface_not_the_placement_origin(self):
        """`solved.y` is the highest terrain cell in the footprint; the pad ends
        up at `flatten_cost.target_y`."""
        self.assertAlmostEqual(CA.surface_y(place()), 70.91, places=2)
        no_cost = place()
        del no_cost["solved"]["flatten_cost"]
        self.assertAlmostEqual(CA.surface_y(no_cost), 70.86, places=2)


class Commands(unittest.TestCase):
    def test_removal_command_names_ids_because_ignore_alone_removes_nothing(self):
        area = CA.clearing_area(place())
        generate, remove = CL.commands(area, CL.DEFAULT_KEEP)
        self.assertIn("id=*", remove)
        self.assertIn("ignore=_*", remove)
        self.assertIn("max=51.50", remove)
        self.assertIn("pos=-4673.5,-310.5", remove)
        self.assertTrue(generate.startswith("zones_generate "))

    def test_generation_reaches_every_zone_the_cylinder_touches(self):
        """`max=` selects a zone by the distance to its CENTRE, so the reach has
        to add a zone's half-diagonal or a corner zone is left ungenerated and
        its trees appear later, when a player arrives."""
        area = CA.clearing_area(place())
        reach = CL.generate_max(area)
        self.assertGreaterEqual(reach, area.radius_m + CL.ZONE_HALF_M * math.sqrt(2) - 1e-6)
        worst_centre = area.radius_m + CL.ZONE_HALF_M * math.sqrt(2)
        self.assertGreaterEqual(reach, worst_centre - 1e-6)

    def test_keep_patterns_protect_engine_objects_and_nothing_else(self):
        self.assertTrue(CL.protected("_ZoneCtrl", CL.DEFAULT_KEEP))
        self.assertTrue(CL.protected("_TerrainCompiler", CL.DEFAULT_KEEP))
        self.assertFalse(CL.protected("Beech1", CL.DEFAULT_KEEP))
        self.assertTrue(CL.protected("Beech1", ("Beech1",)))
        self.assertFalse(CL.protected("Beech1_x", ("Beech1",)))

    def test_truncated_listings_do_not_invent_prefab_names(self):
        """ValheimRcon severs a response at ~4050 bytes.  MEASURED: a bare
        `^-Prefab: (\\S+)` read "Pic" out of a cut "Pickable_Stone" and reported
        it as an object that had not been removed."""
        body = ("Found 2 objects:\n"
                "-Prefab: Beech1 Id: 1:2 Position: (1 2 3)\n"
                "-Prefab: Pic")
        self.assertEqual(CL.PREFAB_RE.findall(body), ["Beech1"])
        self.assertEqual(int(CL.FOUND_RE.search(body).group(1)), 2)


class AgainstTheRealPlacements(unittest.TestCase):
    """Every solved Ulfsland placement must yield a clearable area, because a
    refusal here means the pipeline silently skips a site."""

    def test_every_solved_ulfsland_placement_derives_an_area(self):
        worlds = CLEARING.parent / "worlds" / "Ulfsland"
        checked = 0
        for path in sorted(worlds.glob("*/placements.yaml")):
            doc = yaml.safe_load(path.read_text())
            for entry in doc.get("placements") or []:
                if not entry.get("solved"):
                    continue
                got = CA.clearing_area(entry)
                self.assertTrue(got.covers_rotated_pad(), f"{path.parent.name}/{entry['id']}")
                self.assertGreater(got.verify_half_side_m, 0.0)
                self.assertGreater(got.verify_relief_headroom_m(got.relief_m), -1e9)
                checked += 1
        self.assertGreater(checked, 0, "no solved placements found to check")


if __name__ == "__main__":
    unittest.main()
