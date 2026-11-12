"""Search crops constrain A*, not the original measurement's physical probes."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roads import network


class MeasurementRoleTests(unittest.TestCase):
    def setUp(self):
        n = 128
        axis = np.arange(n, dtype=np.float64) - n / 2 + .5
        x, z = np.meshgrid(axis, axis)
        self.source = network.fieldmod.Field(
            "measured", 0., 0., n / 2, 1., n,
            (40. + .025*x + .05*z).astype(np.float32),
            np.ones((n, n), dtype=np.uint8))
        # A native byte slice, off-centre on BOTH axes.  Search indices must
        # never be used to index the original measurement's terrain arrays.
        i, j, size = 24, 40, 64
        source = self.source
        x0, z0 = source.world(i, j)
        self.crop = replace(
            source, cx=x0 + size/2 - .5, cz=z0 + size/2 - .5,
            half=size/2, n=size,
            h=source.h[i:i+size, j:j+size],
            biome=source.biome[i:i+size, j:j+size])
        self.record = {"patch": source.id, "routing_view": {
            "measurement_patch": source.id,
            "measurement_bounds": [-64., 64., -64., 64.],
            "source_sample_bounds": [i, j, size],
            "view_bounds": [-24., 40., -40., 24.],
        }}
        self.corr = network.routemod.Corridor(
            self.crop, cell_m=1., grade_max=network.routemod.GRADE_CEILING)
        self.corr.forbid = np.zeros((size, size), dtype=bool)
        # This obstacle closes the direct route and every northern detour.
        # The only A* passage is within three cells of the crop's south edge;
        # its shoulder is genuinely measured, but is outside the search crop.
        self.corr.forbid[3:, 28:36] = True
        self.start = self.corr.cell_world(32, 10)
        self.end = self.corr.cell_world(32, 53)
        self.edge = {"id": "crop-detour", "a": "west", "b": "east",
                     "cls": "trunk", "pri": 1, **self.record}
        nodes = {name: {"xz": point, "patch": source.id, "island": "test"}
                 for name, point in (("west", self.start), ("east", self.end))}
        # Isolate the world inputs, not the search/sampler/profile machinery.
        self.enterContext(patch.object(network.specmod, "NODES", nodes))
        self.enterContext(patch.object(network, "_PAD_CLAIMS", []))
        self.enterContext(patch.object(network, "_NODE_ANCHORS", {}))

    def test_masked_detour_profiles_real_measurement_beyond_search_crop(self):
        corr = self.corr
        raw = corr.search(self.start, self.end)
        self.assertTrue(all(not corr.forbid[corr.cell_of(*p)] for p in raw))
        self.assertLess(min(z for _, z in raw), self.crop.z0 + 3.)
        pts, arc = network.routemod.resample(
            network.routemod.simplify(raw, network.SIMPLIFY_TOL_M),
            network.STATION_M)
        self.assertGreater(arc[-1], np.linalg.norm(np.subtract(self.end, self.start)))
        crop_lo, _, crop_off = network.transverse_window(self.crop, pts, 6.)
        self.assertTrue(crop_off)
        with self.assertRaises(ValueError):
            network.build_segment(self.crop, self.edge, corr, [], census=None)

        measured = network._measurement_field({self.source.id: self.source}, self.record)
        lo, hi, off = network.transverse_window(measured, pts, 6.)
        self.assertEqual(off, [])
        self.assertTrue(all(measured.contains(x, z) for _, _, x, z in crop_off))
        # Read actual numeric samples outside the crop, not its clamped edge.
        # The analytic plane supplies an independent expected measurement.
        k, _, x, z = min(crop_off, key=lambda probe: probe[3])
        self.assertAlmostEqual(measured.bilinear(x, z), 40. + .025*x + .05*z,
                               places=5)
        self.assertGreater(abs(measured.bilinear(x, z) - self.crop.bilinear(x, z)), .01)
        self.assertLess(lo[k], crop_lo[k])
        terrain = np.array([measured.bilinear(x, z) for x, z in pts])
        fit = network.routemod.fit_profile(
            terrain, np.asarray(arc), network.routemod.GRADE_CEILING,
            lo=hi-network.routemod.CUT_FILL_MAX,
            hi=lo+network.routemod.CUT_FILL_MAX)
        self.assertEqual(fit["infeasible_spans"], [])
        self.assertTrue(np.all(fit["y"] >= hi-network.routemod.CUT_FILL_MAX-1e-8))
        self.assertTrue(np.all(fit["y"] <= lo+network.routemod.CUT_FILL_MAX+1e-8))
        # Physical coverage did not widen A* or replace any of its arrays.
        self.assertEqual(corr.H.shape, (64, 64))
        self.assertEqual((corr.x0, corr.z0), (self.crop.x0, self.crop.z0))
        self.assertTrue(np.shares_memory(corr.fld.h, self.source.h))

    def test_true_measurement_exhaustion_still_refuses(self):
        measured = network._measurement_field({self.source.id: self.source}, self.record)
        pts = [(-20., -62.), (0., -62.), (20., -62.)]
        _, _, off = network.transverse_window(measured, pts, 6.)
        self.assertTrue(off)
        self.assertTrue(any(not measured.contains(x, z) for _, _, x, z in off))
        with self.assertRaises(ValueError):
            network.build_segment(measured, self.edge, self.corr, [], census=None,
                                  fixed_pts=pts)

    def test_arrival_uses_measurement_with_crop_cell_frame(self):
        # An almost-snapped node beside the southern crop edge.  Its pad can
        # meet the datum, but the whole arrival bench cannot be read on crop.
        node = self.corr.cell_world(1, 10)
        node = (node[0] + .1, node[1])
        pad = {"site_id": "edge-pad", "x": node[0], "z": node[1],
               "hw": 1., "hd": 1.}
        datum = 40. + .025*node[0] + .05*node[1]
        measured = network._measurement_field({self.source.id: self.source}, self.record)
        with patch.object(network, "_ARRIVAL_REACH_MODE", "datum"):
            cropped_cell, cropped = network.arrival_start(
                "edge-pad", node, self.corr, 6., fld=self.crop,
                claims=[pad], datums={"edge-pad": datum})
            measured_cell, physical = network.arrival_start(
                "edge-pad", node, self.corr, 6., fld=measured,
                claims=[pad], datums={"edge-pad": datum})
        self.assertEqual(cropped["verdict"], "UNREAD")
        self.assertEqual(physical["verdict"], "MEETS")
        self.assertEqual(measured_cell, (1, 10))
        self.assertEqual(cropped_cell, measured_cell)

    def test_missing_stale_and_disagreeing_measurements_refuse_before_physics(self):
        source, record = self.source, self.record
        view = record["routing_view"]
        cases = [
            ({}, record),
            ({source.id: replace(source, cx=1.)}, record),
            ({source.id: self.crop}, record),
            ({source.id: replace(source, id="another-survey")}, record),
            ({source.id: source}, {**record, "patch": "another-survey"}),
            ({source.id: source}, {**record, "routing_view": {**view,
                "measurement_bounds": [-64.+1e-7, 64., -64., 64.]}}),
            ({source.id: source}, {**record, "routing_view": {**view,
                "measurement_bounds": [-64., 64., float("nan"), 64.]}}),
            ({source.id: source}, {**record, "routing_view": {**view,
                "measurement_bounds": None}}),
            ({source.id: source}, {**record, "routing_view": {}}),
        ]
        for fields, rec in cases:
            with self.subTest(fields=list(fields), record=rec):
                with self.assertRaises(ValueError):
                    network.transverse_window(network._measurement_field(fields, rec),
                                              [self.start, self.end], 6.)

    def test_uncropped_historical_measurement_alias_reads_original_samples(self):
        source = replace(self.source, id="mainland")
        measured = network._measurement_field({source.id: source}, {"patch": "trunk-primary"})
        lo, hi, off = network.transverse_window(measured, [(-10., -38.), (10., -38.)], 6.)
        self.assertEqual(off, [])
        np.testing.assert_allclose(lo, [40.-.25-2.1, 40.+.25-2.1], atol=1e-5)
        np.testing.assert_allclose(hi, [40.-.25-1.7, 40.+.25-1.7], atol=1e-5)


if __name__ == "__main__":
    unittest.main()
