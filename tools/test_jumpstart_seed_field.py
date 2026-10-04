#!/usr/bin/env python3
"""A FRESH SEED MUST GET ITS OWN GROUND, OR BE TOLD WHO MAKES IT.

Every stage downstream of routing reads a 1 m PatchScan height field.
`roads/field.py::require_seed` has refused a FOREIGN field since the seed became
a parameter -- but a fresh seed's usual state is not "the wrong field", it is NO
FIELD, and that state produced, MEASURED 2026-09-21 before this file existed:

    field.require_seed('/tmp/does-not-exist/Fresh42.bin', 'Fresh42')
      -> FileNotFoundError: [Errno 2] No such file or directory: ...
    field.require_seed(<a zero-byte file>, 'Fresh42')
      -> ValueError: ... is not a VHPATCH1 file

Both true, and neither names a stage that PRODUCES one -- so the cheapest way
past either is to point --field at the field that does exist, which is
Ulfsland's. That is how a fresh seed ends up planning against Pirate68's
coastline, and it is the same unread-declaration class as the 12,301 Pirate68
locations that walked into a Fresh42 run.

The third measurement that day, also before this file existed:

    VH_SEED=Fresh42; site_finder.load_grid(<a grid whose header says Pirate68>)
      -> returned Ulfsland's land, silently

The coarse grid is the FIRST artefact in the fresh-seed chain -- the scan
windows are derived from it -- so an unchecked one puts Ulfsland's geography
under everything downstream.

THE FIXTURES HERE CONSTRUCT THEIR OWN WORLD. No grid, no save, no /tmp dump and
no game install is read: a case that leans on an artefact goes QUIET, not red,
when that artefact moves.
"""

import json
import os
import struct
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent / "jumpstart"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "roads"))

import worldseed  # noqa: E402

field = __import__("field")  # roads/field.py
SF = field._site_finder()

# --------------------------------------------------------------------------
# fixtures: a world made here, not borrowed
# --------------------------------------------------------------------------

GRID_STEP = 8
GRID_EXTENT = 4096  # metres from the origin to a world edge
GRID_N = 2 * GRID_EXTENT // GRID_STEP

SEA = 4.0      # the Ocean clamp
LAND = 40.0    # comfortably over the y=30 water plane


def _cells(x0: float, x1: float, z0: float, z1: float) -> tuple[slice, slice]:
    """Cell slices covering a world-metre rectangle of the fixture grid."""
    i0 = int((z0 + GRID_EXTENT) // GRID_STEP)
    i1 = int((z1 + GRID_EXTENT) // GRID_STEP)
    j0 = int((x0 + GRID_EXTENT) // GRID_STEP)
    j1 = int((x1 + GRID_EXTENT) // GRID_STEP)
    return slice(i0, i1), slice(j0, j1)


def write_grid(path: Path, seed: str, islands: list[tuple], seed_hash: int = 1234) -> Path:
    """A VHBIOME4 grid holding exactly the islands asked for.

    `islands` are `(x0, x1, z0, z1, biome_id)` world-metre rectangles. Wire
    format is site_finder.load_grid's, which is SeedScan's.
    """
    height = np.full((GRID_N, GRID_N), SEA, dtype="<f4")
    biome = np.full((GRID_N, GRID_N), 8, dtype=np.uint8)  # Ocean
    for x0, x1, z0, z1, bid in islands:
        zs, xs = _cells(x0, x1, z0, z1)
        height[zs, xs] = LAND
        biome[zs, xs] = bid
    name = seed.encode()
    blob = (b"VHBIOME4"
            + struct.pack("<iiiii", GRID_STEP, GRID_EXTENT, GRID_N, 2, len(name))
            + name + struct.pack("<i", seed_hash)
            + biome.tobytes() + height.tobytes())
    path.write_bytes(blob)
    return path


def write_locations(path: Path, seed: str, spawn: tuple[float, float] | None) -> Path:
    """A run_locscan.sh-shaped dump declaring its own seed."""
    locs = []
    if spawn is not None:
        locs.append({"name": "StartTemple", "prefab": "StartTemple", "biome": "Meadows",
                     "x": spawn[0], "y": 31.0, "z": spawn[1], "placed": True})
    path.write_text(json.dumps({"seedNameFromWorld": seed, "locations": locs}))
    return path


def write_field(path: Path, seed: str, windows) -> Path:
    """A VHPATCH1 file declaring `seed`, with one flat patch per window."""
    name = seed.encode()
    out = [b"VHPATCH1", struct.pack("<i", 7), struct.pack("<i", len(name)), name,
           struct.pack("<i", len(windows))]
    for w in windows:
        n = 4  # the payload is irrelevant here; the header is what is read
        wid = w.fid.encode()
        out += [struct.pack("<i", len(wid)), wid,
                struct.pack("<ffff", w.cx, w.cz, w.half, w.step),
                struct.pack("<i", n),
                np.full((n, n), LAND, dtype="<f4").tobytes(),
                bytes(n * n)]
    path.write_bytes(b"".join(out))
    return path


# Two islands, and the SMALLER one holds the spawn -- because "trunk-primary is
# the window holding the spawn point" and a derivation that just took the
# biggest landmass would pass a same-size fixture and be wrong here.
SPAWN_ISLAND = (-3000.0, -1000.0, -3000.0, -1000.0, 1)     # 2.0 x 2.0 km, Meadows
BIGGER_ISLAND = (0.0, 2600.0, 0.0, 2600.0, 5)              # 2.6 x 2.6 km, Plains
SKERRY = (3400.0, 3600.0, 3400.0, 3600.0, 1)               # 0.04 km2, under the floor
SPAWN_XZ = (-2500.0, -2500.0)


class SeedScopedGrid(unittest.TestCase):
    """The coarse grid is an artefact with a seed in its header. Read it."""

    def setUp(self):
        self.tmp = Path(os.environ.get("PYTEST_TMP", "/tmp")) / "seedfield_tests"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.saved = os.environ.get("VH_SEED")

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("VH_SEED", None)
        else:
            os.environ["VH_SEED"] = self.saved

    def test_foreign_grid_is_refused(self):
        # KNOWN-BAD, measured before the guard: this returned Pirate68's land to
        # a Fresh42 run and said nothing.
        p = write_grid(self.tmp / "foreign.biome", "Pirate68", [SPAWN_ISLAND])
        os.environ["VH_SEED"] = "Fresh42"
        with self.assertRaises(SystemExit) as cm:
            SF.load_grid(p)
        msg = str(cm.exception)
        self.assertIn("Pirate68", msg)
        self.assertIn("Fresh42", msg)
        self.assertIn("run_scan.sh", msg)

    def test_own_grid_is_accepted(self):
        # The other direction: a guard that refused everything would pass the
        # case above and break every real run.
        p = write_grid(self.tmp / "own.biome", "Fresh42", [SPAWN_ISLAND])
        os.environ["VH_SEED"] = "Fresh42"
        self.assertEqual(SF.load_grid(p)["seed"], "Fresh42")

    def test_unasked_run_keeps_working(self):
        # No --seed and no $VH_SEED: the expectation is the grid's own seed, so
        # a bare local solve on any grid still works. This is the direction that
        # would break every existing caller if the guard were absolute.
        os.environ.pop("VH_SEED", None)
        p = write_grid(self.tmp / "bare.biome", "Whatever77", [SPAWN_ISLAND])
        self.assertEqual(SF.load_grid(p)["seed"], "Whatever77")

    def test_the_grid_guard_is_load_bearing(self):
        # NEUTER: take the refusal out and the reader goes back to what it
        # MEASURABLY did -- hand Pirate68's land to a Fresh42 run and say
        # nothing. Proves the case above tests the guard and not the fixture.
        p = write_grid(self.tmp / "foreign.biome", "Pirate68", [SPAWN_ISLAND])
        os.environ["VH_SEED"] = "Fresh42"
        saved = worldseed.require_scope
        worldseed.require_scope = lambda *a, **k: {}
        try:
            self.assertEqual(SF.load_grid(p)["seed"], "Pirate68")
        finally:
            worldseed.require_scope = saved


class LandComponents(unittest.TestCase):
    """The measurement: this seed's own landmasses, off its own height plane."""

    @classmethod
    def setUpClass(cls):
        tmp = Path("/tmp/seedfield_tests")
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ.pop("VH_SEED", None)
        cls.grid = SF.load_grid(
            write_grid(tmp / "world.biome", "Fresh42",
                       [SPAWN_ISLAND, BIGGER_ISLAND, SKERRY]))

    def test_every_island_is_found_biggest_first(self):
        comps = SF.land_components(self.grid)
        self.assertEqual(len(comps), 3)
        self.assertEqual([c["biome"] for c in comps], ["Plains", "Meadows", "Meadows"])
        areas = [c["area_m2"] for c in comps]
        self.assertEqual(areas, sorted(areas, reverse=True))
        # 2.6 km square, measured in 32 m blocks: within one block per side.
        self.assertAlmostEqual(areas[0], 2600.0 ** 2, delta=2 * 32 * 2600)

    def test_bounding_box_and_centroid_are_world_metres(self):
        biggest = SF.land_components(self.grid)[0]
        self.assertAlmostEqual(biggest["x0"], 0.0, delta=32.0)
        self.assertAlmostEqual(biggest["x1"], 2600.0, delta=32.0)
        self.assertAlmostEqual(biggest["cx"], 1300.0, delta=48.0)
        self.assertAlmostEqual(biggest["cz"], 1300.0, delta=48.0)

    def test_component_name_follows_the_ulfsland_convention(self):
        # `plains23` is land component 23, mostly Plains. A fresh seed's ids are
        # built the same way, so a window id still says what it is.
        biggest = SF.land_components(self.grid)[0]
        self.assertEqual(biggest["name"], f"plains{biggest['label']}")

    def test_spawn_resolves_to_the_island_it_stands_on(self):
        comp = SF.component_at(self.grid, SPAWN_XZ)
        self.assertIsNotNone(comp)
        self.assertEqual(comp["biome"], "Meadows")
        self.assertLessEqual(comp["x0"], SPAWN_XZ[0])
        self.assertGreaterEqual(comp["x1"], SPAWN_XZ[0])

    def test_open_water_is_on_no_landmass(self):
        self.assertIsNone(SF.component_at(self.grid, (3900.0, -3900.0)))


class DerivedWindows(unittest.TestCase):
    """The geometry: windows from THIS seed's land, in the established naming."""

    def setUp(self):
        tmp = Path("/tmp/seedfield_tests")
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ.pop("VH_SEED", None)
        self.g = SF.load_grid(write_grid(tmp / "world.biome", "Fresh42",
                                         [SPAWN_ISLAND, BIGGER_ISLAND, SKERRY]))
        self.comps = SF.land_components(self.g)
        self.spawn_comp = SF.component_at(self.g, SPAWN_XZ)

    def derive(self, **kw):
        return worldseed.windows_for_components(
            self.comps, SPAWN_XZ, self.spawn_comp["label"], **kw)

    def test_trunk_primary_is_the_spawn_landmass_not_the_biggest(self):
        w = self.derive()
        self.assertEqual(w[0].fid, worldseed.TRUNK_PRIMARY)
        self.assertEqual(w[0].cx, SPAWN_XZ[0])
        self.assertEqual(w[0].cz, SPAWN_XZ[1])
        self.assertIn("Meadows", w[0].role)          # the spawn island's biome
        self.assertEqual(w[1].fid, worldseed.TRUNK_SECONDARY)
        self.assertIn("Plains", w[1].role)           # the bigger one, second

    def test_the_spawn_is_inside_its_own_window(self):
        # The definition of trunk-primary, asserted as geometry: a window named
        # for the spawn that does not contain it is the same kind of false
        # declaration as a seed literal.
        w = self.derive()[0]
        self.assertLessEqual(abs(w.cx - SPAWN_XZ[0]), w.half)
        self.assertLessEqual(abs(w.cz - SPAWN_XZ[1]), w.half)

    def test_half_extent_covers_the_landmass_it_is_for(self):
        w = self.derive()[0]
        comp = self.spawn_comp
        self.assertGreaterEqual(w.cx + w.half, comp["x1"])
        self.assertLessEqual(w.cx - w.half, comp["x0"])
        self.assertGreaterEqual(w.cz + w.half, comp["z1"])
        self.assertLessEqual(w.cz - w.half, comp["z0"])

    def test_a_clamped_window_says_it_is_clamped(self):
        w = self.derive(half_max_m=400.0)[0]
        self.assertEqual(w.half, 400.0)
        self.assertIn("CLAMPED", w.role)
        self.assertIn("covers", w.role)

    def test_coverage_is_an_intersection_not_an_area_ratio(self):
        # KNOWN-BAD, measured on the first Fresh42 drive: coverage was
        # `(2*half)^2 / bbox_area`, so outlier-plains215 -- bbox 2656 x 3680 m
        # under a 3200 m window -- reported "covers 100%" while 480 m of its
        # own coast sat outside the window. A ratio of areas cannot see an axis.
        comp = {"label": 3, "cells": 4000, "area_m2": 4.2e6, "name": "plains3",
                "biome": "Plains", "x0": -1328.0, "x1": 1328.0,
                "z0": -1840.0, "z1": 1840.0, "cx": 0.0, "cz": 0.0}
        w = worldseed.windows_for_components([comp], (0.0, 0.0), 3)[0]
        self.assertEqual(w.half, 1600.0)
        self.assertIn("CLAMPED", w.role)
        self.assertNotIn("100%", w.role)
        # x fits whole (2656 < 3200), z is cut to 3200 of 3680 = 87%.
        self.assertIn(f"covers {3200 / 3680 * 100:.0f}%", w.role)

    def test_a_window_records_its_water_gap_not_just_its_overlap(self):
        # KNOWN-BAD, and it stopped a peer's stage: `trunk-secondary` was
        # DEFINED as "reached from trunk-primary by bridge rather than by
        # portal", which is a fact about Ulfsland -- its two trunk landmasses
        # are a 12 m channel apart and their windows OVERLAP. Fresh42's are
        # 4,346.6 m apart and its windows do not touch. A consumer inferring
        # "bridgeable" from overlapping windows gets Ulfsland's topology on
        # every seed, so the window carries the MEASURED water gap instead.
        w = self.derive(separation_of=lambda label: 4346.6)
        self.assertNotIn("open water", w[0].role)        # the spawn's own
        self.assertIn("4,347 m of open water", w[1].role)
        self.assertIn("NOT walkable", w[1].role)
        # and the id itself still promises nothing about how you get there
        self.assertNotIn("bridge", w[1].role)

    def test_skerry_is_below_the_declared_floor(self):
        ids = [w.fid for w in self.derive()]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all("skerry" not in i for i in ids))
        # and it is the FLOOR that dropped it, not luck: lower the floor and the
        # third window appears with an outlier id.
        ids = [w.fid for w in self.derive(min_area_m2=1000.0)]
        self.assertEqual(len(ids), 3)
        self.assertTrue(ids[2].startswith("outlier-meadows"))

    def test_a_budget_it_cannot_meet_refuses_instead_of_scanning_less(self):
        with self.assertRaises(SystemExit) as cm:
            self.derive(budget_bytes=1024)
        msg = str(cm.exception)
        self.assertIn("NOTHING IS DROPPED SILENTLY", msg)
        self.assertIn("budget_bytes", msg)

    def test_no_spawn_refuses_and_names_the_stage(self):
        with self.assertRaises(SystemExit) as cm:
            worldseed.windows_for_components(self.comps, None, None)
        self.assertIn("run_locscan.sh", str(cm.exception))

    def test_all_land_below_the_floor_refuses(self):
        with self.assertRaises(SystemExit) as cm:
            self.derive(min_area_m2=1e12)
        self.assertIn("floor", str(cm.exception))

    def test_derived_windows_are_a_patchscan_request(self):
        rows = worldseed.request_rows(self.derive())
        self.assertEqual(len(rows), 2)
        for rid, cx, cz, half, step in rows:
            self.assertIsInstance(rid, str)
            self.assertGreater(half, 0.0)
            self.assertEqual(step, 1.0)


class WindowCost(unittest.TestCase):
    """The budget is arithmetic on the wire format, not a guess."""

    def test_sample_count_is_patchscans_own_formula(self):
        # PatchScan.cs:291  int n = round(2*half/step), floored at 2.
        self.assertEqual(worldseed.window_samples(1600.0, 1.0), 3200)
        self.assertEqual(worldseed.window_samples(0.4, 1.0), 2)

    def test_ulfslands_own_seven_windows_price_out_to_the_file_on_disk(self):
        # /tmp/roads/h1m.bin is 230,562,258 bytes. The patch BODIES are
        # 230,562,000 of that; the remaining 258 is the VHPATCH1 header plus the
        # seven per-patch descriptors. Pinned as a literal rather than read off
        # that file: a case that leans on an artefact goes quiet when it moves.
        self.assertEqual(worldseed.windows_bytes(worldseed.ULFSLAND_WINDOWS),
                         230_562_000)


class FieldIsProducedNeverBorrowed(unittest.TestCase):
    """Absent, foreign and undeclared are all refusals, and they say who makes one."""

    def setUp(self):
        self.tmp = Path("/tmp/seedfield_tests")
        self.tmp.mkdir(parents=True, exist_ok=True)

    def test_absent_field_names_the_producing_stage(self):
        # KNOWN-BAD, measured: FileNotFoundError [Errno 2], which names nobody.
        with self.assertRaises(SystemExit) as cm:
            field.require_seed(self.tmp / "nothing-here.bin", "Fresh42")
        msg = str(cm.exception)
        self.assertIn("Fresh42", msg)
        self.assertIn("run_scan.sh", msg)
        self.assertIn("request_for_seed", msg)
        self.assertIn("run_patchscan.sh", msg)

    def test_empty_field_names_the_producing_stage(self):
        # KNOWN-BAD, measured: "is not a VHPATCH1 file", which names nobody.
        p = self.tmp / "truncated.bin"
        p.write_bytes(b"")
        with self.assertRaises(SystemExit) as cm:
            field.require_seed(p, "Fresh42")
        self.assertIn("run_patchscan.sh", str(cm.exception))

    def test_the_absence_guard_is_load_bearing(self):
        # NEUTER: put the old behaviour back and the instruction disappears,
        # leaving the errno that made borrowing Ulfsland's field the cheap way
        # out. Proves the case above is testing the guard, not the filesystem.
        saved = field.require_field
        field.require_field = lambda path, seed: Path(path)
        try:
            with self.assertRaises(FileNotFoundError):
                field.require_seed(self.tmp / "nothing-here.bin", "Fresh42")
        finally:
            field.require_field = saved

    def test_foreign_field_is_refused(self):
        p = write_field(self.tmp / "pirate.bin", "Pirate68",
                        worldseed.ULFSLAND_WINDOWS[:1])
        with self.assertRaises(SystemExit) as cm:
            field.require_seed(p, "Fresh42")
        self.assertIn("Pirate68", str(cm.exception))

    def test_undeclared_field_is_refused(self):
        # "It did not say" is exactly how Ulfsland's numbers walk into a fresh
        # build; an empty declaration is not permission.
        p = write_field(self.tmp / "undeclared.bin", "", worldseed.ULFSLAND_WINDOWS[:1])
        with self.assertRaises(SystemExit):
            field.require_seed(p, "Fresh42")

    def test_own_field_is_accepted(self):
        p = write_field(self.tmp / "fresh.bin", "Fresh42",
                        worldseed.ULFSLAND_WINDOWS[:1])
        self.assertEqual(field.require_seed(p, "Fresh42")["seed"], "Fresh42")


class DefaultPathUnchanged(unittest.TestCase):
    """Pirate68 must produce what it produced yesterday, byte for byte."""

    def test_ulfsland_windows_are_untouched(self):
        self.assertEqual([w.fid for w in worldseed.ULFSLAND_WINDOWS][:2],
                         [worldseed.TRUNK_PRIMARY, worldseed.TRUNK_SECONDARY])
        self.assertEqual(len(worldseed.ULFSLAND_WINDOWS), 7)

    def test_the_request_tsv_is_byte_identical_to_the_one_that_was_run(self):
        # This is /tmp/roads/req1m.tsv as it was actually run for Pirate68,
        # rendered by roads/field.py::request's own format string. Pinned here
        # as text: if a derivation ever starts supplying the default path, this
        # is what changes first.
        rows = worldseed.request_rows(worldseed.ULFSLAND_WINDOWS, functional=False)
        tsv = "".join(f"{pid}\t{cx:g}\t{cz:g}\t{half:g}\t{step:g}\n"
                      for pid, cx, cz, half, step in rows)
        self.assertEqual(tsv, (
            "mainland\t798\t600\t1600\t1\n"
            "westisle\t-760\t1188\t1600\t1\n"
            "south33\t1742\t-3302\t1700\t1\n"
            "blackforest6\t-3292\t-3714\t1300\t1\n"
            "plains23\t578\t-4478\t700\t1\n"
            "workshop3054\t-4134\t-906\t950\t1\n"
            "mtn7114\t46\t3668\t660\t1\n"))

    def test_worldseed_selftest_still_passes(self):
        worldseed.selftest(verbose=False)


if __name__ == "__main__":
    unittest.main()
