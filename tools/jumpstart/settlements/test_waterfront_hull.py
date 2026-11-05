#!/usr/bin/env python3
"""Read-only numeric and measured-corpus full-hull berth regressions.

Run this file with unittest class names to select NumericHullTests or
MeasuredCorpusHullTests. ACCEPT_PIECE_GEOMETRY selects the real corpus normally.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

JUMPSTART = Path(__file__).resolve().parents[1]
if str(JUMPSTART) not in sys.path:
    sys.path.insert(0, str(JUMPSTART))
from settlements import waterfront as WF
from accept.sections import harbours as H


def field(n=129, depth=4.0):
    heights = np.full((n, n), WF.W.WATER_LEVEL - depth, np.float32)
    return WF.W.Field("constructed", 0.0, 0.0, n / 2.0, 1.0, n,
                      heights, np.ones((n, n), np.uint8))


def surface(fld, hulls):
    return SimpleNamespace(patch_for=lambda *args, **kwargs: fld,
                           _measured_hulls=hulls)


def sample_points(result):
    fld, ii, jj, mask = result
    return set(zip(fld.world_x(jj[mask]).tolist(),
                   fld.world_z(ii[mask]).tolist()))


def depth_consumers(fields, hulls):
    """Real Surface/adapter consumers over one explicit immutable numeric bed."""
    import north_transport as NT
    import north_itinerary as NI
    import shores
    base = WF.Surface.__new__(WF.Surface)
    base.fields, base.field_names = fields, ["numeric field"]
    native = NT.Surface.__new__(NT.Surface)
    native.fields, native.field_names = fields, ["numeric field"]
    native.applied = SimpleNamespace(writes=[], delta_at=lambda x, z: 0.0)
    native._hull_fields, native._hull_masks = {}, {}
    generated = shores.GeneratedSurface(fields)
    sources = {key: SimpleNamespace(
        id=f.id, cx=f.cx, cz=f.cz, half=f.half, step=f.step, n=f.n,
        x0=f.x0, z0=f.z0, h=f.heights, biome=f.biomes)
        for key, f in fields.items()}
    with patch.object(NI.worldseed, "seed", return_value="numeric-hull-fixture"):
        planned, _ = NI.planned_surface(sources, [], None)
    for owner in (base, native, generated, planned):
        owner._measured_hulls = hulls
    return [("waterfront", lambda yaw, x, z, vessel: WF.hull_depths(
                base, yaw, x, z, vessel)),
            ("transport", native.hull_depths), ("shores", generated.hull_depths),
            ("itinerary", planned.hull_depths)]


def nested_pool_fields():
    """A small survey ends in deep inland water; only its parent sees the bank."""
    from dataclasses import replace
    fields = {}
    for n in (33, 129):
        fld = replace(field(n=n), id=f"nested-{n}")
        ii, jj = np.mgrid[:n, :n]
        pool = (np.abs(fld.world_x(jj)) <= 18.0) & (
            np.abs(fld.world_z(ii)) <= 18.0)
        fld.heights[~pool] = 40.0
        fields[fld.id] = fld
    return fields


class NumericHullTests(unittest.TestCase):
    def setUp(self):
        self.vessel = next(iter(WF.VESSELS))
        # A collider displaced forwards AND sideways from its prefab root.
        self.hull = H.Hull(self.vessel, 4.0, 8.0,
                           WF.VESSELS[self.vessel]["draught_m"], None,
                           WF.KEEL_CLEARANCE_M, "numeric collider fixture",
                           ((2.0, 4.0), (6.0, 4.0), (6.0, 12.0), (2.0, 12.0)))
        self.hulls = {self.vessel: self.hull}
        self.fld = field()
        self.surf = surface(self.fld, self.hulls)

    def test_offset_rotated_masks_match_shared_authority(self):
        for root in ((0.0, 0.0), (1.37, -2.41), (-13.19, 8.63)):
            for yaw in (0.0, 37.0, 90.0, 181.0, 270.0):
                with self.subTest(root=root, yaw=yaw):
                    got = WF.hull_footprint(self.surf, yaw, *root, self.vessel)
                    self.assertIsNotNone(got)
                    self.assertEqual(sample_points(got),
                                     set(H.footprint(self.hull, *root, yaw)))
        self.assertNotIn((0.0, 0.0), sample_points(
            WF.hull_footprint(self.surf, 0.0, 0.0, 0.0, self.vessel)))

    def test_measured_corner_controls_scalar_depth_and_connectivity(self):
        corner = max(H.footprint(self.hull, 0.0, 0.0, 0.0))
        self.fld.heights[self.fld.index(*corner)] = WF.W.WATER_LEVEL - 0.1
        got = WF.hull_depths(self.surf, 0.0, 0.0, 0.0, self.vessel,
                             open_sea=np.ones_like(self.fld.heights, dtype=bool))
        self.assertAlmostEqual(got[0], 0.1, places=4)
        self.assertTrue(got[2])  # Caller-supplied connectivity remains independent.
        connected = WF.hull_depths(self.surf, 0.0, 0.0, 0.0, self.vessel)
        self.assertFalse(connected[2])
        self.assertLess(connected[0], self.hull.required_depth_m)

    def test_clear_berth_preserves_tuple_and_samples(self):
        got = WF.hull_depths(self.surf, 37.0, 1.37, -2.41, self.vessel)
        self.assertEqual(len(got), 5)
        self.assertEqual(got[:3], (4.0, 4.0, True))
        self.assertEqual(got[3], len(H.footprint(self.hull, 1.37, -2.41, 37.0)))
        self.assertIs(got[4], self.fld)

    def test_missing_or_degenerate_geometry_refuses_by_vessel(self):
        for profile in ((), ((0.0, 0.0),), ((0.0, 0.0), (1.0, 0.0))):
            from dataclasses import replace
            bad = replace(self.hull, profile=profile)
            with self.subTest(profile=profile), self.assertRaisesRegex(
                    ValueError, self.vessel + ": no measured full-hull footprint"):
                WF.hull_depths(surface(self.fld, {self.vessel: bad}),
                               0.0, 0.0, 0.0, self.vessel)
        with self.assertRaisesRegex(ValueError, self.vessel):
            WF.hull_depths(surface(self.fld, {}), 0.0, 0.0, 0.0, self.vessel)

    def test_partial_coverage_is_not_a_clipped_clear_berth(self):
        small = field(n=17)
        self.assertIsNone(WF.hull_depths(surface(small, self.hulls),
                                       0.0, 0.0, 0.0, self.vessel))
        for yaw in (0.0, 37.0, 90.0, 270.0):
            self.assertIsNone(WF.hull_depths(self.surf, yaw, 63.0, 63.0,
                                           self.vessel))

    def test_missing_height_and_wrong_connectivity_coverage_refuse(self):
        point = H.footprint(self.hull, 0.0, 0.0, 0.0)[0]
        self.fld.heights[self.fld.index(*point)] = np.nan
        self.assertIsNone(WF.hull_depths(self.surf, 0.0, 0.0, 0.0, self.vessel))
        self.fld.heights[:] = 26.0
        with self.assertRaisesRegex(ValueError, "open-sea mask"):
            WF.hull_depths(self.surf, 0.0, 0.0, 0.0, self.vessel,
                           open_sea=np.ones((3, 3), bool))

    def test_coarse_or_unaligned_fields_do_not_subsample_the_hull(self):
        from dataclasses import replace
        for fld in (replace(self.fld, step=2.0),
                    replace(self.fld, cx=0.5)):
            with self.subTest(step=fld.step, cx=fld.cx):
                self.assertIsNone(WF.hull_depths(surface(fld, self.hulls),
                                               0.0, 0.0, 0.0, self.vessel))

    def test_berth_search_moves_off_a_measured_offset_corner_shoal(self):
        surf = WF.Surface.__new__(WF.Surface)
        surf.fields, surf.field_names = {self.fld.id: self.fld}, ["numeric field"]
        surf._measured_hulls = self.hulls
        corner = max(H.footprint(self.hull, 0.0, 0.0, 0.0))
        self.fld.heights[self.fld.index(*corner)] = WF.W.WATER_LEVEL - 0.1
        seeded = WF.hull_depths(surf, 0.0, 0.0, 0.0, self.vessel)
        self.assertLess(seeded[0], self.hull.required_depth_m)
        self.assertFalse(seeded[2])
        site = dict(kind="slip", bearing=0.0)
        seed = dict(x=0.0, z=0.0, source="numeric pier head", planned_prefab=None)
        with patch.object(WF, "SITES", {"constructed": site}), \
                patch.object(WF, "berth_seed", return_value=seed):
            got = WF.berth_spot(surf, "constructed", self.vessel)
        self.assertTrue(got["floats"])
        self.assertTrue(got["clears_margin_floor"])
        self.assertGreater(got["moved_from_seed_m"], 0.0)
        selected = WF.hull_depths(surf, got["yaw"], got["x"], got["z"], self.vessel)
        self.assertTrue(selected[2])
        self.assertGreaterEqual(selected[0],
                                self.hull.required_depth_m + WF.BERTH_MARGIN_FLOOR_M)

    def test_native_adapters_refuse_rotated_measured_offset_corner_shoals(self):
        for yaw in (0.0, 37.0, 90.0):
            fld = field()
            corner = max(H.footprint(self.hull, 0.0, 0.0, yaw))
            fld.heights[fld.index(*corner)] = WF.W.WATER_LEVEL - 0.1
            for name, measure in depth_consumers({fld.id: fld}, self.hulls):
                with self.subTest(consumer=name, yaw=yaw, corner=corner):
                    got = measure(yaw, 0.0, 0.0, self.vessel)
                    self.assertAlmostEqual(got[0], 0.1, places=4)
                    self.assertEqual(got[1], 4.0)
                    self.assertFalse(got[2])

    def test_nested_surveys_cannot_make_an_inland_pool_open_sea(self):
        vessel = max(WF.VESSELS, key=lambda name: WF.VESSELS[name]["hull_l"])
        hull = H.Hull(vessel, 6.0, 8.0, WF.VESSELS[vessel]["draught_m"], None,
                      WF.KEEL_CLEARANCE_M, "numeric centred collider fixture",
                      ((-3.0, -4.0), (3.0, -4.0), (3.0, 4.0), (-3.0, 4.0)))
        fields = nested_pool_fields()
        for name, measure in depth_consumers(fields, {vessel: hull}):
            with self.subTest(consumer=name):
                got = measure(0.0, 0.0, 0.0, vessel)
                self.assertEqual(got[:2], (4.0, 4.0))
                self.assertFalse(got[2], "the surrounding dry bank must block open sea")

    def test_corpus_override_belongs_to_each_surface_snapshot(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "measured.json"
            def publish(z_end):
                corpus.write_text(json.dumps({"prefabs": {self.vessel: [
                    ["b", 2.0, -1.0, 4.0, 6.0, 1.0, z_end, "box"]]},
                    "build_pieces": []}))
            publish(12.0)
            with patch.dict(os.environ, {"ACCEPT_PIECE_GEOMETRY": str(corpus)}):
                first = SimpleNamespace(patch_for=lambda *args, **kwargs: self.fld)
                original = WF.measured_hulls(first)[self.vessel]
                self.assertEqual(set(original.profile), set(self.hull.profile))
                publish(16.0)
                self.assertIs(WF.measured_hulls(first)[self.vessel], original)
                second = SimpleNamespace(patch_for=lambda *args, **kwargs: self.fld)
                refreshed = WF.measured_hulls(second)[self.vessel]
                self.assertEqual(max(z for _, z in refreshed.profile), 16.0)
                self.assertEqual(max(z for _, z in original.profile), 12.0)

    def test_live_probe_requests_share_full_integer_footprints(self):
        from dataclasses import replace
        import tempfile
        import harbour_city as HC
        hulls = {name: replace(self.hull, name=name) for name in WF.VESSELS}
        berths = [dict(id=f"numeric-{i}", x=x, z=z, yaw=yaw)
                  for i, (x, z, yaw) in enumerate(
                      ((0.37, -0.41, 0.0), (7.23, 3.68, 37.0)))]
        empty_plan = SimpleNamespace(sorted_rows=lambda: [])
        design = {"_plan": empty_plan, "_plan_data": empty_plan}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(HC, "VERIFY_DIR", Path(directory)), \
                patch.object(HC, "U_BERTHS", berths), \
                patch.object(H, "hull_table", return_value=hulls):
            path, rows = HC.verify_points(design)
            self.assertTrue(path.is_file())
            hull_rows = [row for row in rows if row["kind"] == "hull"]
            for berth in berths:
                names = {row["vessel"] for row in hull_rows
                         if row["berth"] == berth["id"]}
                self.assertEqual(names, {"CargoShip", "WarShip", "VikingShip", "MercantShip"})
                for name in names:
                    got = {(row["x"], row["z"]) for row in hull_rows
                           if (row["berth"], row["vessel"]) == (berth["id"], name)}
                    self.assertEqual(got, set(H.footprint(
                        hulls[name], berth["x"], berth["z"], berth["yaw"])))
                    self.assertTrue(all(x == round(x) and z == round(z)
                                        for x, z in got))
            missing = {name: replace(hull, profile=()) for name, hull in hulls.items()}
            with patch.object(H, "hull_table", return_value=missing):
                with self.assertRaisesRegex(ValueError, "no measured full-hull footprint"):
                    HC.verify_points(design)


class MeasuredCorpusHullTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hulls = H.hull_table()

    def test_every_required_vessel_has_clear_measured_root_frame_coverage(self):
        fld = field()
        surf = surface(fld, self.hulls)
        self.assertEqual(set(self.hulls), set(WF.VESSELS))
        for name, hull in self.hulls.items():
            for yaw in (0.0, 37.0, 90.0, 181.0):
                with self.subTest(vessel=name, yaw=yaw):
                    self.assertGreaterEqual(len(hull.profile), 3)
                    got = WF.hull_depths(surf, yaw, 0.37, -0.41, name)
                    self.assertIsNotNone(got)
                    self.assertEqual(got[:3], (4.0, 4.0, True))
                    self.assertGreaterEqual(got[0], hull.required_depth_m)
                    self.assertEqual(sample_points(WF.hull_footprint(
                        surf, yaw, 0.37, -0.41, name)),
                        set(H.footprint(hull, 0.37, -0.41, yaw)))

    def test_nested_surveys_preserve_measured_odin_inland_pool_refusal(self):
        hulls = {name: self.hulls[name] for name in WF.ODIN_SHIPS
                 if WF.VESSELS[name]["hull_l"] > 16.0
                 and self.hulls[name].radius_m + 1.0 <= 16.0}
        self.assertTrue(hulls, "the measured corpus needs a hull fitting the nested survey")
        for vessel, hull in hulls.items():
            for name, measure in depth_consumers(nested_pool_fields(), {vessel: hull}):
                with self.subTest(vessel=vessel, consumer=name):
                    got = measure(0.0, 0.0, 0.0, vessel)
                    self.assertEqual(got[:2], (4.0, 4.0))
                    self.assertFalse(got[2], "a measured hull in an inland pool cannot reach sea")

    def test_actual_odin_corners_omitted_by_old_ellipse_cannot_hide_shoals(self):
        # Select actual measured lattice corners geometrically, not by a ship
        # name, saved-world landmark, hand-picked vertex, or test-only hull.
        exposed = set()
        for name in sorted(WF.ODIN_SHIPS):
            hull = self.hulls[name]
            spec = WF.VESSELS[name]
            for bx, bz, yaw in ((0.37, -0.41, 0.0), (7.23, 3.68, 37.0),
                                (-4.17, 6.29, 90.0)):
                ux, uz = WF.A._unit(yaw)
                def old_ellipse_radius(point):
                    dx, dz = point[0] - bx, point[1] - bz
                    return ((dx * ux + dz * uz) / (spec["hull_l"] / 2.0)) ** 2 \
                        + ((dx * uz - dz * ux) / (spec["hull_w"] / 2.0)) ** 2
                missed = [p for p in H.footprint(hull, bx, bz, yaw)
                          if old_ellipse_radius(p) > 1.0 + 1e-6]
                if not missed:
                    continue
                corner = max(missed, key=old_ellipse_radius)
                exposed.add(name)
                with self.subTest(vessel=name, root=(bx, bz), yaw=yaw, corner=corner):
                    fld = field(depth=3.0)
                    fld.heights[fld.index(*corner)] = WF.W.WATER_LEVEL - 0.1
                    surf = surface(fld, self.hulls)
                    got = WF.hull_depths(surf, yaw, bx, bz, name,
                        open_sea=np.ones_like(fld.heights, dtype=bool))
                    self.assertAlmostEqual(got[0], 0.1, places=4)
                    self.assertLess(got[0], hull.required_depth_m)
                    connected = WF.hull_depths(surf, yaw, bx, bz, name)
                    self.assertFalse(connected[2])
        self.assertTrue(exposed, "measured Odin corpus has no corner outside the old ellipse")


if __name__ == "__main__":
    unittest.main()
