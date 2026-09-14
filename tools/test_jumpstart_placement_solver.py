#!/usr/bin/env python3
"""Tests for the placement solver's honesty contract.

The defect these pin down, MEASURED on Ulfsland/Pirate68 before the fix:
`pre-bonemass/placements.yaml` declared `near: Bonemass, within_m: 1400` and
recorded `anchor_dist_m: 3629`. The seed has FIVE Bonemass altars; the solver
collapsed them to whichever one sat nearest the world origin, measured against
that one, found nothing inside 1400 m of it, quietly multiplied `within_m` by
three, and wrote the result with no indication that the answer disagreed with
the question. The site it picked was in fact 484 m from a different altar.

So there are two contracts worth a test:
  * a multi-instance anchor is a SET, and a requirement is satisfied if any
    instance satisfies it;
  * when no site satisfies the requirement, the solved block says so in the
    file -- `satisfies_requirement: false` plus a `violations:` list with the
    size of the miss -- instead of burying it in a relaxation multiplier.
"""

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SOLVER = Path(__file__).resolve().parent / "jumpstart" / "blueprints" / "solve_placements.py"
sys.path.insert(0, str(SOLVER.parent))

import site_finder as SF  # noqa: E402

STEP = 8
N = 128
EXTENT = STEP * N // 2  # 512
MEADOWS = 1
OCEAN = 8
# Valheim's water plane is y = 30 in WorldGenerator.GetHeight units, so a
# fixture has to use ABSOLUTE heights: "10 m" is 20 m under water, not land.
LAND = 40.0
SEABED = 10.0


def cell_centre(i: int) -> float:
    return -EXTENT + i * STEP + STEP * 0.5


def write_grid(path: Path, biome, height, seed: str = "TestSeed") -> None:
    """Emit a VHBIOME4 grid the way tools/seedscan/run_scan.sh does."""
    name = seed.encode("utf-8")
    with path.open("wb") as fh:
        fh.write(b"VHBIOME4")
        fh.write(struct.pack("<iiiii", STEP, EXTENT, N, 2, len(name)))
        fh.write(name)
        fh.write(struct.pack("<i", 4242))
        fh.write(biome.astype("uint8").tobytes())
        fh.write(height.astype("<f4").tobytes())


def write_locations(path: Path, locations) -> None:
    path.write_text(json.dumps({"locations": locations}), "utf-8")


def build_world(tmp: Path, requirement: dict) -> Path:
    world = tmp / "TestWorld"
    (world / "preset-a").mkdir(parents=True)
    (world / "preset-a" / "placements.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "world": "TestWorld",
                "preset": "preset-a",
                "placements": [
                    {
                        "id": "test-hall",
                        "blueprint": "none.blueprint",
                        "requirement": requirement,
                        "rotation": {"yaw": 0},
                    }
                ],
            },
            sort_keys=False,
        ),
        "utf-8",
    )
    return world


def run_solver(world: Path, grid: Path, locs: Path, seed: str = "TestSeed"):
    return subprocess.run(
        [
            sys.executable,
            str(SOLVER),
            "--world", str(world),
            "--seed", seed,
            "--grid", str(grid),
            "--locations", str(locs),
            "--manifest", "/nonexistent",
            "--no-refine",
            "--write",
        ],
        capture_output=True,
        text=True,
    )


def solved_block(world: Path) -> dict:
    doc = yaml.safe_load((world / "preset-a" / "placements.yaml").read_text("utf-8"))
    return doc["placements"][0]["solved"]


class TwoAnchorInstances(unittest.TestCase):
    """Rough ground around the near altar, a flat pad around the far one."""

    def setUp(self) -> None:
        import numpy as np

        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        xs = np.array([cell_centre(i) for i in range(N)])
        zz, xx = np.meshgrid(xs, xs, indexing="ij")

        biome = np.full((N, N), OCEAN, dtype="uint8")
        height = np.full((N, N), SEABED, dtype="float32")

        # near altar at the origin: Meadows, but corrugated 3 m so a 2 m
        # flatness budget can never be met anywhere around it
        near = np.hypot(xx, zz) <= 220
        biome[near] = MEADOWS
        checker = ((np.arange(N)[:, None] + np.arange(N)[None, :]) % 2).astype("float32")
        height[near] = (LAND + 3.0 * checker)[near]

        # far altar: a buildable Meadows pad on a gentle 1:20 ramp. Over the
        # 32 m footprint that is exactly 1.6 m of spread -- inside a 2.0 m
        # budget, outside a 0.5 m one, which is what lets one fixture serve both
        # "the requirement is satisfiable, find it" and "it is not, say so".
        far = np.hypot(xx + 400, zz + 400) <= 120
        biome[far] = MEADOWS
        height[far] = (LAND + 0.05 * (xx + 400.0))[far]

        self.grid = tmp / "test.biome"
        self.locs = tmp / "loc.json"
        write_grid(self.grid, biome, height)
        write_locations(
            self.locs,
            [
                {"name": "Altar", "prefab": "Altar", "biome": "Meadows", "x": 0.0, "y": 10.0, "z": 0.0},
                {"name": "Altar", "prefab": "Altar", "biome": "Meadows", "x": -400.0, "y": 10.0, "z": -400.0},
            ],
        )
        self.tmp = tmp

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_requirement_is_met_against_whichever_instance_satisfies_it(self) -> None:
        world = build_world(
            self.tmp,
            {
                "biome": "Meadows",
                "footprint_m": 32,
                "max_flat_m": 2.0,
                "min_biome_purity": 1.0,
                "coastal": False,
                "location_clearance_m": 0,
                "near": "Altar",
                "within_m": 150,
            },
        )
        proc = run_solver(world, self.grid, self.locs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        solved = solved_block(world)

        # the pad is 565 m from the origin altar and inside 150 m of the far one
        self.assertLessEqual(solved["anchor_dist_m"], 150)
        self.assertIn("-400", solved["anchor"])
        self.assertTrue(solved["satisfies_requirement"])
        self.assertNotIn("violations", solved)

    def test_roughness_is_a_bill_not_a_violation(self) -> None:
        # Nowhere in this world is a 32 m footprint flatter than 1.6 m: the ramp
        # is the best ground there is and the corrugated field is 3 m. Under a
        # 0.5 m budget the OLD solver called that a violation and refused every
        # site. It is not a violation -- the placement declares
        # `flatten_required: true` and is levelled on arrival -- so the solver
        # must place it, satisfy every HARD constraint, and price the work.
        world = build_world(
            self.tmp,
            {
                "biome": "Meadows",
                "footprint_m": 32,
                "max_flat_m": 0.5,
                "min_biome_purity": 1.0,
                "coastal": False,
                "location_clearance_m": 0,
                "near": "Altar",
                "within_m": 150,
            },
        )
        run_solver(world, self.grid, self.locs)
        solved = solved_block(world)
        # flatness is COSTED: it must not appear as a violation
        self.assertNotIn(
            "max_flat_m", [v["constraint"] for v in solved.get("violations", [])], solved
        )
        self.assertTrue(solved["satisfies_requirement"], solved)
        # ...and the bill must be reported, in material actually moved
        cost = solved["flatten_cost"]
        self.assertGreater(cost["moved_m3"], 0.0)
        self.assertGreater(cost["mean_move_m"], 0.0)
        self.assertAlmostEqual(
            cost["moved_m3"], cost["cut_m3"] + cost["fill_m3"], places=0
        )
        self.assertGreaterEqual(cost["max_cut_m"], 0.0)
        # the hard constraints it did satisfy: dry, in biome, near the anchor
        self.assertGreaterEqual(solved["freeboard_m"], SF.DEFAULT_FREEBOARD)
        self.assertLessEqual(solved["anchor_dist_m"], 150)
        self.assertEqual(solved["biome_purity"], 1.0)

    def test_a_hard_constraint_is_never_costed_away(self) -> None:
        # Freeboard is hard. Demand 30 m of it -- nothing in this world has that
        # -- and the solver must report it as a violation rather than pricing it,
        # and must say what would work instead.
        world = build_world(
            self.tmp,
            {
                "biome": "Meadows",
                "footprint_m": 32,
                "max_flat_m": 3.0,
                "min_biome_purity": 1.0,
                "coastal": False,
                "location_clearance_m": 0,
                "min_height_m": 30.0,
                "near": "Altar",
                "within_m": 150,
            },
        )
        run_solver(world, self.grid, self.locs)
        solved = solved_block(world)
        self.assertFalse(solved["satisfies_requirement"], solved)
        self.assertIn(
            "min_height_m", [v["constraint"] for v in solved["violations"]], solved
        )
        self.assertIn("remedy", solved)


class ClearanceBeforeTruncation(unittest.TestCase):
    """Clearance is a hard constraint, not a post-filter on the flattest few."""

    def setUp(self) -> None:
        import numpy as np

        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        xs = np.array([cell_centre(i) for i in range(N)])
        zz, xx = np.meshgrid(xs, xs, indexing="ij")

        biome = np.full((N, N), OCEAN, dtype="uint8")
        height = np.full((N, N), SEABED, dtype="float32")

        # a large, perfectly flat Meadows field sitting on top of a location:
        # thousands of cells that beat everything else on flatness and are all
        # inside the clear area. Filtering clearance after a flatness sort finds
        # nothing here at all.
        blocked = np.hypot(xx - 200, zz - 200) <= 240
        biome[blocked] = MEADOWS
        height[blocked] = LAND

        # the only legitimate site: flat, far from any location, slightly worse
        # on nothing at all -- it simply loses the flatness tie-break by tenths
        clear = np.hypot(xx + 350, zz + 350) <= 90
        biome[clear] = MEADOWS
        height[clear] = LAND + 10.1

        self.grid = tmp / "test.biome"
        self.locs = tmp / "loc.json"
        write_grid(self.grid, biome, height)
        write_locations(
            self.locs,
            [
                {"name": "Crypt", "prefab": "SunkenCrypt4", "biome": "Meadows",
                 "x": 200.0, "y": 10.0, "z": 200.0},
                {"name": "Altar", "prefab": "Altar", "biome": "Meadows",
                 "x": 0.0, "y": 10.0, "z": 0.0},
            ],
        )
        self.tmp = tmp

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_site_inside_a_location_clear_area_is_never_chosen(self) -> None:
        world = build_world(
            self.tmp,
            {
                "biome": "Meadows",
                "footprint_m": 32,
                "max_flat_m": 1.0,
                "min_biome_purity": 1.0,
                "coastal": False,
                "location_clearance_m": 200,
                "near": "Altar",
                "within_m": 700,
            },
        )
        proc = run_solver(world, self.grid, self.locs)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        solved = solved_block(world)
        # 200 m of stand-off plus the footprint's half-diagonal
        self.assertGreaterEqual(solved["nearest_location_m"], 200 + 32 * 2 ** 0.5 / 2)
        self.assertTrue(solved["satisfies_requirement"], solved)
        self.assertLess(solved["x"], 0.0)
        self.assertLess(solved["z"], 0.0)


class WaterDatum(unittest.TestCase):
    """A site must clear Valheim's water plane, which is y = 30, not y = 0.

    This is the bug `SpawnOnLand` caught: the solver compared terrain height
    against 0, so "on land" meant "not 30 m under water" and 10 of 13 solved
    bases were below the surface -- `sandbox-harbour` by 22 m. The fixture here
    is the smallest thing that would have caught it: a dead-flat, pure Meadows
    pad at y = 30.4, which is 0.4 m under the game's own land threshold of
    water + 1 m, sitting beside an unambiguously dry one.
    """

    def setUp(self) -> None:
        import numpy as np

        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        xs = np.array([cell_centre(i) for i in range(N)])
        zz, xx = np.meshgrid(xs, xs, indexing="ij")

        biome = np.full((N, N), OCEAN, dtype="uint8")
        height = np.full((N, N), SEABED, dtype="float32")

        # shallows: flat, pure Meadows, and 0.4 m proud of the water plane --
        # above zero, so the old datum called it land
        shallow = np.hypot(xx - 150, zz - 150) <= 200
        biome[shallow] = MEADOWS
        height[shallow] = 30.4

        # genuinely dry ground, further from the anchor so it only wins on being
        # out of the water
        dry = np.hypot(xx + 350, zz + 350) <= 150
        biome[dry] = MEADOWS
        height[dry] = LAND

        self.grid = tmp / "test.biome"
        self.locs = tmp / "loc.json"
        write_grid(self.grid, biome, height)
        write_locations(
            self.locs,
            [{"name": "Altar", "prefab": "Altar", "biome": "Meadows",
              "x": 0.0, "y": 40.0, "z": 0.0}],
        )
        self.tmp = tmp

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _solve(self):
        world = build_world(
            self.tmp,
            {
                "biome": "Meadows",
                "footprint_m": 32,
                "max_flat_m": 1.0,
                "min_biome_purity": 1.0,
                "coastal": False,
                "location_clearance_m": 0,
                "near": "Altar",
                "within_m": 900,
            },
        )
        run_solver(world, self.grid, self.locs)
        return solved_block(world)

    def test_a_pad_below_the_water_plane_is_not_chosen(self) -> None:
        solved = self._solve()
        # the dry pad is the one at negative x/z; the shallow pad is nearer the
        # anchor and perfectly flat, so it wins on every other axis
        self.assertLess(solved["x"], 0.0, solved)
        self.assertLess(solved["z"], 0.0, solved)
        self.assertGreater(solved["y"], 30.0 + SF.DEFAULT_FREEBOARD, solved)
        self.assertGreater(solved["freeboard_m"], 0.0, solved)

    def test_freeboard_is_reported_against_the_water_plane(self) -> None:
        solved = self._solve()
        self.assertAlmostEqual(solved["freeboard_m"], LAND - 30.0, places=1)
        self.assertTrue(solved["satisfies_requirement"], solved)

    def test_water_level_constant_matches_the_game(self) -> None:
        self.assertEqual(SF.WATER_LEVEL, 30.0)


class StalePatchRefused(unittest.TestCase):
    def test_refine_rejects_a_patch_cut_around_a_different_site(self) -> None:
        import numpy as np

        patch = {
            "cx": 500.0,
            "cz": -500.0,
            "half": 32.0,
            "step": 1.0,
            "n": 64,
            "height": np.full((64, 64), 10.0, dtype="float32"),
            "biome": np.full((64, 64), MEADOWS, dtype="uint8"),
        }
        cand = {"x": 0.0, "z": 0.0, "flat": 1.0, "biome_purity": 1.0,
                "water_dist": 50.0, "anchor_dist": 10.0, "location_dist": 500.0}
        req = {"biome": "Meadows", "footprint_m": 32, "max_flat_m": 2.0}
        with self.assertRaises(ValueError) as ctx:
            SF.refine(cand, patch, req, None, None, {"n": N, "step": STEP, "extent": EXTENT})
        self.assertIn("stale", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
