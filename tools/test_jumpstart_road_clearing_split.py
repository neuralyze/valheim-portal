#!/usr/bin/env python3
"""Tests for the removal-disc split that keeps a road clearing away from things
it may not delete WITHOUT abandoning the trees standing in the road.

THE DEFECT THESE PIN DOWN, MEASURED on Ulfsland on 2026-09-16.  The operator's
complaint was "roads still have trees in them".  Two segments --
T12-wtspawn-temple, the road out of the respawn point, and T3-temple-meadhall
-- had been cleared and then had their own terrain written over the objects the
clearing missed: 22 and 15 objects standing inside the derived clearing width,
13 of T12's BURIED up to 3.818 m under the carriageway.

The cause was not a missing gate.  `plan_removals` refuses any cylinder holding
a prefab it may not delete, and -- correctly -- it shrinks rather than skipping,
because one stray beam should not abandon a whole disc.  But it shrank the disc
CONCENTRICALLY, to `min(nearest blocker) - TERRAIN_SPREAD_M`, and that is the
right rule with the wrong geometry: a blocker near the CENTRE erases the disc.
T12's derived disc at (77.43, -67.43) with radius 12.57 m met a generated wood
house 4.71 m from its centre, so what was actually sent (seq 937 and 952) had
radius 3.84 m.  It removed 4 objects and left 14 standing.  Nothing was
recorded as skipped, because nothing was skipped -- a shrink is silent, which
is why the defect was reported to the operator as fixed.

So three things have to be right, and each is the kind of thing that answers
confidently while measuring the wrong quantity:

  * THE STANDOFF IS STILL WHOLE.  The point of the old shrink was that no
    cylinder comes within `TERRAIN_SPREAD_M` of a blocker.  A split that
    covers more ground by reaching closer to a blocker has not fixed the
    defect, it has traded a buried tree for a deleted POI -- the one
    unrepairable damage class.  The test is the same shape as
    `footprint_clear`'s: `dist(centre, blocker) >= radius + standoff`, for
    EVERY blocker and EVERY sub-disc.
  * THE OVER-REACH IS STILL BOUNDED.  A disc's radius is the written footprint
    at that station plus a 0.35 FRACTION of it, so a sub-disc that reached
    outside the parent would clear verge nothing measured.  Every sub-disc must
    be a strict subset of its parent.
  * THE RADIUS IS FLOORED, NOT ROUNDED.  `max=` goes on the wire at two
    decimals and `round` goes up half the time, which spends up to 5 mm of the
    standoff that is the whole guarantee.  MEASURED before the floor existed:
    T12's worst emitted cylinder cleared its nearest blocker by 0.997 m
    against a 1.0 m standoff, i.e. the rounding decided it rather than the
    geometry.

What is NOT proven here: that `objects_remove` deletes what the wire names.
That is proven against the live server by each record's own postcondition --
`objects_count` for exactly those prefabs in exactly that cylinder, total 0,
tolerance 0 -- and a unit test cannot tell you that `id=*` also deletes
whatever arrived since the census.
"""

import math
import sys
import unittest
from pathlib import Path

JUMPSTART = Path(__file__).resolve().parent / "jumpstart"
sys.path.insert(0, str(JUMPSTART / "roads"))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART))


def _load(name: str, path: Path):
    """Import a module by PATH under a unique name.

    Two `clear.py` exist - `jumpstart/clearing` (sites) and `jumpstart/roads`
    (road corridors) - and `sys.modules` caches by the BARE name, so a bare
    `import clear` after a `sys.path.insert` gives whichever one was imported
    FIRST to every later importer. MEASURED: roads then clearing returns the
    roads module for both. Each file passes alone; a combined run fails, or
    silently tests the wrong module. Load by path under a distinct name.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {name} from {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CL = _load("jumpstart_road_clear", JUMPSTART / "roads" / "clear.py")


def obj(prefab, x, z, oid, y=0.0, lat=0.0, half=9.0):
    return {"prefab": prefab, "id": oid, "x": x, "z": z, "y": y,
            "lat_m": lat, "clear_half_m": half,
            "clearable": CL.is_clearable(prefab), "in_clear_width": True}


# The T12 geometry that produced the defect, taken from the live census: the
# derived parent disc, the generated wood house's nearest piece, and the
# objects that were left standing inside the parent.
T12_PARENT = (77.43, -67.43, 12.57)
T12_BLOCKERS = [
    obj("wood_wall_roof_top_45", 72.78, -68.18, "b1"),
    obj("woodwall", 72.78, -68.18, "b2"),
    obj("wood_wall_roof_45", 72.78, -66.18, "b3"),
    obj("wood_beam_45", 72.78, -64.18, "b4"),
    obj("wood_floor", 71.78, -68.18, "b5"),
    obj("wood_roof_45", 71.78, -67.18, "b6"),
]
T12_TARGETS = [
    obj("Beech1", 75.24, -63.00, "t1"),
    obj("Beech_small1", 75.63, -63.60, "t2"),
    obj("Beech_small2", 75.00, -71.89, "t3"),
    obj("Beech_small2", 76.90, -71.81, "t4"),
    obj("Pickable_Branch", 76.86, -62.52, "t5"),
    obj("Rock_3", 78.98, -60.17, "t6"),
    obj("Rock_4", 83.11, -63.75, "t7"),
    obj("Beech1", 73.60, -68.02, "t8"),
]


class SubDiscStandoff(unittest.TestCase):
    """The guarantee the old concentric shrink existed to give, kept."""

    def test_every_subdisc_clears_every_blocker_by_the_standoff(self):
        cx, cz, r0 = T12_PARENT
        subs, _un = CL.split_around_blockers(cx, cz, r0, T12_TARGETS,
                                             T12_BLOCKERS)
        self.assertTrue(subs, "the split produced no cylinder at all")
        for s in subs:
            sx, sz = s["centre"]
            for b in T12_BLOCKERS:
                d = math.hypot(sx - b["x"], sz - b["z"])
                self.assertGreaterEqual(
                    d, s["radius_m"] + CL.BLOCKER_STANDOFF_M,
                    f"sub-disc {s['centre']} r{s['radius_m']} reaches within "
                    f"{d - s['radius_m']:.3f} m of {b['prefab']}, inside the "
                    f"{CL.BLOCKER_STANDOFF_M} m standoff")

    def test_the_radius_is_floored_so_rounding_cannot_eat_the_standoff(self):
        # One blocker, one target, and a clearance chosen so the honest radius
        # has a third decimal: 3.137 m rounds UP to 3.14 and spends 3 mm of
        # the standoff, and floors to 3.13 and does not.
        cx, cz, r0 = 0.0, 0.0, 12.0
        blocker = obj("wood_floor", 4.137, 0.0, "b")
        target = obj("Beech1", 0.0, 0.0, "t")
        subs, _un = CL.split_around_blockers(cx, cz, r0, [target], [blocker])
        self.assertEqual(len(subs), 1)
        self.assertLessEqual(
            subs[0]["radius_m"], 4.137 - CL.BLOCKER_STANDOFF_M,
            "the sent radius is larger than the honest clearance, so the "
            "standoff was decided by rounding")
        self.assertEqual(subs[0]["radius_m"] * 100.0,
                         math.floor(subs[0]["radius_m"] * 100.0),
                         "the radius must be expressible at the two decimals "
                         "the wire carries")

    def test_every_subdisc_stays_inside_its_parent(self):
        cx, cz, r0 = T12_PARENT
        subs, _un = CL.split_around_blockers(cx, cz, r0, T12_TARGETS,
                                             T12_BLOCKERS)
        for s in subs:
            sx, sz = s["centre"]
            reach = math.hypot(sx - cx, sz - cz) + s["radius_m"]
            self.assertLessEqual(
                reach, r0 + 1e-9,
                f"sub-disc {s['centre']} r{s['radius_m']} reaches "
                f"{reach:.2f} m from the parent centre, outside the parent's "
                f"{r0} m -- that is verge no measurement covered")


class SubDiscCoverage(unittest.TestCase):
    """And it has to actually remove the trees, which is the whole point."""

    def test_it_reaches_the_objects_the_concentric_shrink_abandoned(self):
        cx, cz, r0 = T12_PARENT
        # What the old rule sent: one disc pulled in to the nearest blocker.
        nearest = min(math.hypot(b["x"] - cx, b["z"] - cz)
                      for b in T12_BLOCKERS)
        concentric = nearest - CL.TERRAIN_SPREAD_M
        old = [t for t in T12_TARGETS
               if math.hypot(t["x"] - cx, t["z"] - cz) <= concentric]
        subs, un = CL.split_around_blockers(cx, cz, r0, T12_TARGETS,
                                            T12_BLOCKERS)
        covered = {i for s in subs for i in s["covered_ids"]}
        self.assertEqual(len(old), 0,
                         "the concentric shrink is expected to abandon all of "
                         "these; if it does not, the fixture no longer "
                         "reproduces the measured defect")
        self.assertGreaterEqual(
            len(covered), 7,
            f"the split reaches only {len(covered)} of {len(T12_TARGETS)}; "
            f"the measured live pass reached 7 of these 8")
        self.assertEqual(covered | {u["id"] for u in un},
                         {t["id"] for t in T12_TARGETS},
                         "every target must be either covered or named as "
                         "unreachable -- an object that is neither is a tree "
                         "left in the road with no reason recorded")

    def test_an_unreachable_object_names_the_blocker_that_binds_it(self):
        cx, cz, r0 = T12_PARENT
        _subs, un = CL.split_around_blockers(cx, cz, r0, T12_TARGETS,
                                             T12_BLOCKERS)
        self.assertTrue(un, "the Beech1 0.84 m from a roof piece cannot be "
                            "reached by any blocker-clear cylinder, so the "
                            "fixture must produce an unreachable object")
        for u in un:
            self.assertIn("prefab", u["nearest_blocker"])
            self.assertLess(
                u["nearest_blocker"]["dist_m"],
                CL.BLOCKER_STANDOFF_M + CL.SUBDISC_MIN_RADIUS_M + 1e-9,
                "an object is only unreachable when its nearest blocker "
                "leaves less than the minimum sendable cylinder; anything "
                "else is a coverage bug wearing a reason")
            self.assertTrue(u["why"].strip(),
                            "left standing with an empty reason is the defect "
                            "this whole pass exists to stop reporting as fixed")


class UnblockedDiscsAreUntouched(unittest.TestCase):
    """The thirteen segments cleared before the split must keep their geometry:
    a tile with nothing non-clearable in it still gets its full derived radius,
    so re-planning them cannot silently change what they would clear."""

    def test_a_tile_with_no_blocker_keeps_its_full_derived_radius(self):
        seg = {"id": "synthetic", "width_m": 8.0,
               "nodes": [[0.0, 0.0], [40.0, 0.0]],
               "profile_y": [10.0, 10.0], "terrain_y": [10.0, 10.0],
               "is_bridge": [False, False], "length_m": 40.0}
        # The DERIVED radius, asked of the geometry rather than written down:
        # `plan_removals` derives its own discs (a census recorded before
        # `cover_gaps` existed carries the marched cover alone), so a literal
        # here would pin the fixture's invented tile list instead of the
        # contract -- which is that an unblocked tile is neither shrunk nor
        # split.
        per, _widest = CL.clear_half_width(seg)
        derived = {r for _x, _z, r in CL.tiles_local(seg, per)}
        cen = {"segment": "synthetic",
               "tiles": [[10.0, 0.0, 9.0], [30.0, 0.0, 9.0]],
               "objects": [obj("Beech1", 10.0, 3.0, "a"),
                           obj("Rock_4", 30.0, -2.0, "b")]}
        plan = CL.plan_removals(seg, cen)
        self.assertTrue(plan["cylinders_clear"])
        for c in plan["cylinders_clear"]:
            self.assertIn(c["radius_m"], derived,
                          "an unblocked tile must keep the radius the width "
                          "derivation gives it")
            self.assertIsNone(c.get("split_of"))
            self.assertIsNone(c.get("shrunk_from_m"))
        self.assertEqual(plan["left_standing"], 0)
        self.assertEqual(plan["left_standing_objects"], [])

    def test_the_derived_cover_reaches_the_object_the_march_walked_past(self):
        """THE COVERING ARGUMENT IS PLANE GEOMETRY ABOUT A STRAIGHT STRIP and
        the march steps along ARC, so a bend tighter than one step walks past
        the outside of it and the union has a hole there.

        The fixture is the MEASURED case, not an invented bend: T4's own nodes
        300..350, which carry the hairpin whose apex is node 328 at
        (539.99, 706.01), and the probe is a real censused Beech_small2 at
        (548.97, 705.91) -- lateral 8.981 m against that station's derived
        half width of 9.001 m, so INSIDE the clearing width, and 4.84 m
        outside the nearest marched disc.  A synthetic right angle does NOT
        reproduce it (checked: the marched cover closes a 90 degree corner at
        this width), which is exactly why the fixture is the road.
        """
        import math as _m

        import yaml as _yaml
        doc = _yaml.safe_load(
            (JUMPSTART / "roads" / "segments.yaml").read_text())
        t4 = [s for s in doc["segments"]
              if s["id"] == "T4-meadhall-stathub"][0]
        a, b = 300, 350
        seg = {"id": "t4-hairpin", "width_m": t4["width_m"],
               "nodes": t4["nodes"][a:b], "profile_y": t4["profile_y"][a:b],
               "terrain_y": t4["terrain_y"][a:b],
               "is_bridge": t4["is_bridge"][a:b]}
        seg["length_m"] = sum(_m.dist(seg["nodes"][i], seg["nodes"][i + 1])
                              for i in range(len(seg["nodes"]) - 1))
        per, _widest = CL.clear_half_width(seg)
        px, pz = 548.97, 705.91
        discs = CL.tiles_local(seg, per)
        near = min(_m.hypot(px - cx, pz - cz) - r for cx, cz, r in discs)
        self.assertLessEqual(
            near, 0.0,
            f"the censused Beech_small2 at ({px}, {pz}) is inside its "
            f"station's derived clearing width and the nearest removal disc "
            f"still falls {near:.2f} m short of it: the cover is argued "
            f"rather than measured, which is the T4 tiling hole that left 11 "
            f"objects standing with no recorded reason")


if __name__ == "__main__":
    unittest.main()
