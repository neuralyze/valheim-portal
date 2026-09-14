#!/usr/bin/env python3
"""Tests for the jumpstart spawn contract: a spawn is on land, or there is none.

The defects these pin down, both MEASURED on Ulfsland/Pirate68 before the fix:

  * `pre-bonemass/charactertemplate.yml` shipped `spawn: [{x: -2187, y: 33,
    z: 2100}]`, derived from a placement that had since been re-solved 7 km
    away. Nothing compared the template against the placements it came from, so
    the operator's new character woke up on terrain at 26.74 -- 3.26 m BELOW
    Valheim's water plane -- and swam. `derive.py check` must fail on exactly
    that: a recorded spawn whose base has moved.

  * the coordinate the solver offered instead, `spawn_points: [[4560, 29,
    -232]]`, was a naive footprint-and-yaw offset with no terrain test at all,
    and it sampled 25.82 -- 4.2 m under water. Regenerating from it would have
    made the bug worse, so the spawn must be chosen against terrain and the
    naive offset must lose when it is wet.

Everything here runs offline: the terrain is a synthetic VHPATCH1 patch written
in the same layout tools/jumpstart/blueprints/PatchScan.cs emits, so no Valheim
server is booted and no live file is touched.
"""

import contextlib
import importlib.util
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

TOOLS = Path(__file__).resolve().parent
DERIVE_PATH = TOOLS / "jumpstart" / "worlds" / "derive.py"
REAL_WORLD = TOOLS / "jumpstart" / "worlds" / "Ulfsland" / "world.yaml"
PRESET = "pre-bonemass"
PLACEMENT = "test-hall"

# Valheim's water plane in WorldGenerator.GetHeight units. The whole point of
# the fix; see derive.SEA_LEVEL_M for the three measurements behind it.
SEA = 30.0
SWAMP = 2


def load_derive():
    spec = importlib.util.spec_from_file_location("jumpstart_derive", DERIVE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["jumpstart_derive"] = module
    spec.loader.exec_module(module)
    return module


derive = load_derive()


def write_patch(path: Path, patches, seed: str = "TestSeed") -> None:
    """Emit a VHPATCH1 file the way run_patchscan.sh's plugin does."""
    name = seed.encode("utf-8")
    with path.open("wb") as fh:
        fh.write(b"VHPATCH1")
        fh.write(struct.pack("<i", 4242))
        fh.write(struct.pack("<i", len(name)))
        fh.write(name)
        fh.write(struct.pack("<i", len(patches)))
        for pid, cx, cz, half, step, height in patches:
            ident = pid.encode("utf-8")
            n = height.shape[0]
            fh.write(struct.pack("<i", len(ident)))
            fh.write(ident)
            fh.write(struct.pack("<ffff", cx, cz, half, step))
            fh.write(struct.pack("<i", n))
            fh.write(height.astype("<f4").tobytes())
            fh.write(np.full(n * n, SWAMP, dtype=np.uint8).tobytes())


def terrain(half: float, cx: float, cz: float, dry_where) -> np.ndarray:
    """Height plane for one patch: +3.2 m of freeboard where dry, -1 m where not."""
    n = int(round(2 * half))
    xs = cx - half + np.arange(n) + 0.5
    zs = cz - half + np.arange(n) + 0.5
    X, Z = np.meshgrid(xs, zs)
    return np.where(dry_where(X, Z), SEA + 3.2, SEA - 1.0)


def placement_doc(x: float = 0.5, z: float = 0.5, y: float = 33.2) -> dict:
    return {
        "schema_version": 1,
        "world": "TestWorld",
        "preset": PRESET,
        "seed": "TestSeed",
        "placements": [
            {
                "id": PLACEMENT,
                "footprint_xzy_m": [20.0, 20.0, 8.0],
                "requirement": {"footprint_m": 24},
                "rotation": {"units": "euler_degrees_yxz", "yaw": 0},
                "containers": {"slots_floor": 60},
                "solved": {"seed": "TestSeed", "x": x, "y": y, "z": z},
                # What the placement solver offers and this tool refuses to
                # trust: footprint/2 + 6 along the facade, no terrain test.
                "spawn_points": [[0, 34, 16]],
            }
        ],
    }


class SpawnProbe(unittest.TestCase):
    """The chooser itself, against synthetic 1 m terrain."""

    def setUp(self):
        self.half = float(
            np.ceil(np.hypot(10.0, 10.0) + derive.SPAWN_SEARCH_MAX_M + derive.SPAWN_PROBE_PAD_M)
        )
        self.pl = placement_doc()["placements"][0]

    def patch(self, dry_where):
        height = terrain(self.half, 0.5, 0.5, dry_where)
        return {
            "cx": 0.5,
            "cz": 0.5,
            "half": self.half,
            "step": 1.0,
            "n": height.shape[0],
            "height": height,
        }

    def test_picks_dry_ground_and_rejects_the_naive_wet_offset(self):
        # Dry everywhere except the strip beyond z = 12, which is where the
        # solver's own naive offset (0, 16) lands. yaw is 0, so that strip is
        # the facade side: the point the old pipeline would have shipped.
        result = derive.probe_spawn(self.patch(lambda X, Z: Z <= 12.0), self.pl)
        self.assertIn("point", result, result.get("refused"))
        point = result["point"]

        self.assertEqual([type(point[k]) for k in "xyz"], [int, int, int])
        self.assertGreaterEqual(point["spawn_freeboard_m"], derive.SPAWN_FREEBOARD_M)
        self.assertNotEqual((point["x"], point["z"]), (0, 16))
        self.assertLessEqual(point["z"], 12)
        # Outside the footprint by at least the stand-off, and still at the base.
        self.assertGreaterEqual(point["dist_to_footprint_edge_m"], derive.SPAWN_FOOTPRINT_CLEAR_M)
        self.assertLessEqual(point["dist_to_footprint_edge_m"], derive.SPAWN_SEARCH_MAX_M)
        # y is ceil(terrain + clearance), which is also above the water plane.
        self.assertEqual(point["y"], int(np.ceil(point["terrain_y"] + derive.SPAWN_Y_CLEAR_M)))
        self.assertGreater(point["y"], SEA)
        self.assertTrue(point["approach"]["dry_to_footprint_edge"])

    def test_refuses_when_the_base_is_below_the_water_plane(self):
        # Dry land exists 40 m north, but the placement itself is submerged, so
        # every walk to its footprint crosses water. Refuse, do not approximate.
        result = derive.probe_spawn(self.patch(lambda X, Z: Z >= 40.0), self.pl)
        self.assertNotIn("point", result)
        self.assertIn("BELOW the water plane", result["refused"])
        self.assertLess(result["counts"]["base_freeboard_m"], 0.0)

    def test_refuses_when_there_is_no_dry_ground_at_all(self):
        result = derive.probe_spawn(self.patch(lambda X, Z: Z > 1e9), self.pl)
        self.assertNotIn("point", result)
        self.assertIn("freeboard", result["refused"])

    def test_samples_the_exact_integer_that_gets_written(self):
        # The lattice IS the candidate set: patch sample (0, 0) sits on an
        # integer world coordinate, so nothing moves between verdict and write.
        patch = self.patch(lambda X, Z: Z <= 12.0)
        x0, z0 = derive.patch_origin(patch)
        self.assertEqual((x0, z0), (int(x0), int(z0)))
        point = derive.probe_spawn(patch, self.pl)["point"]
        j, i = point["x"] - x0, point["z"] - z0
        self.assertAlmostEqual(float(patch["height"][i, j]), point["terrain_y"], places=2)


class SpawnCrossCheck(unittest.TestCase):
    """`check` must fail when a template's spawn no longer matches placements."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.world_root = root / "TestWorld"
        (self.world_root / PRESET).mkdir(parents=True)
        world_yaml = (
            REAL_WORLD.read_text("utf-8")
            .replace("world: Ulfsland", "world: TestWorld", 1)
            .replace("seed: Pirate68", "seed: TestSeed", 1)
        )
        (self.world_root / "world.yaml").write_text(world_yaml, "utf-8")
        self.placements = self.world_root / PRESET / "placements.yaml"
        self.write_placements(placement_doc())

        self.patch_path = root / "patch.bin"
        half = float(
            np.ceil(np.hypot(10.0, 10.0) + derive.SPAWN_SEARCH_MAX_M + derive.SPAWN_PROBE_PAD_M)
        )
        write_patch(
            self.patch_path,
            [
                (
                    f"{PRESET}#{PLACEMENT}",
                    0.5,
                    0.5,
                    half,
                    1.0,
                    terrain(half, 0.5, 0.5, lambda X, Z: Z <= 12.0),
                )
            ],
        )
        self._here = derive.HERE
        derive.HERE = self.world_root.parent
        self.addCleanup(setattr, derive, "HERE", self._here)
        self.addCleanup(self.tmp.cleanup)

    def write_placements(self, doc) -> None:
        self.placements.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = derive.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def build_everything(self) -> dict:
        code, _, err = self.cli(
            "spawn", "TestWorld", "--preset", PRESET, "--patch", str(self.patch_path), "--reuse-patch"
        )
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("build", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 0, err)
        return yaml.safe_load((self.world_root / PRESET / derive.SPAWN_FILE).read_text("utf-8"))

    def test_template_carries_the_verified_point_and_check_passes(self):
        spawn_doc = self.build_everything()
        point = spawn_doc["points"][0]
        template = (self.world_root / PRESET / "charactertemplate.yml").read_text("utf-8")
        self.assertIn(f"{{x: {point['x']}, y: {point['y']}, z: {point['z']}}}", template)
        # The naive offset the solver recorded is wet, so it must not ship.
        self.assertNotIn("{x: 0, y: 34, z: 16}", template)
        code, _, err = self.cli("check", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 0, err)

    def test_check_fails_when_the_placement_moves_under_the_spawn(self):
        spawn_doc = self.build_everything()
        point = spawn_doc["points"][0]
        self.write_placements(placement_doc(x=900.5, z=-400.5))

        code, out, err = self.cli("check", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 1)
        # The message has to name which preset, what the spawn is, where the
        # base is NOW, and what to run -- that is the whole point of it.
        self.assertIn(f"TestWorld/{PRESET}", err)
        self.assertIn(f"({point['x']}, {point['y']}, {point['z']})", err)
        self.assertIn("900.5", err)
        self.assertIn("-400.5", err)
        self.assertIn(f"derive.py spawn TestWorld --preset {PRESET}", err)
        # And the stale template must be held back rather than re-published.
        self.assertIn("HELD", out)

    def test_check_fails_when_a_hand_edited_spawn_sits_in_the_water(self):
        self.build_everything()
        path = self.world_root / PRESET / derive.SPAWN_FILE
        doc = yaml.safe_load(path.read_text("utf-8"))
        doc["points"][0]["terrain_y"] = SEA + 0.17  # the operator's waterline
        doc["points"][0]["y"] = int(np.ceil(SEA + 0.17 + derive.SPAWN_Y_CLEAR_M))
        path.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")

        code, _, err = self.cli("check", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 1)
        self.assertIn("spawn in the water", err)

    def test_check_fails_when_a_solved_placement_has_no_spawn_decision(self):
        self.build_everything()
        doc = placement_doc()
        doc["placements"].append(
            {
                "id": "second-hall",
                "footprint_xzy_m": [20.0, 20.0, 8.0],
                "requirement": {"footprint_m": 24},
                "rotation": {"yaw": 0},
                "solved": {"seed": "TestSeed", "x": 300.5, "y": 33.2, "z": 300.5},
            }
        )
        self.write_placements(doc)

        code, _, err = self.cli("check", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 1)
        self.assertIn("second-hall", err)
        self.assertIn("neither a verified spawn nor a recorded refusal", err)

    def test_refused_placement_leaves_the_vanilla_spawn_alone(self):
        half = float(
            np.ceil(np.hypot(10.0, 10.0) + derive.SPAWN_SEARCH_MAX_M + derive.SPAWN_PROBE_PAD_M)
        )
        write_patch(
            self.patch_path,
            [
                (
                    f"{PRESET}#{PLACEMENT}",
                    0.5,
                    0.5,
                    half,
                    1.0,
                    terrain(half, 0.5, 0.5, lambda X, Z: Z >= 40.0),
                )
            ],
        )
        code, out, err = self.cli(
            "spawn", "TestWorld", "--preset", PRESET, "--patch", str(self.patch_path), "--reuse-patch"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("NO SPAWN", out)
        code, _, err = self.cli("build", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 0, err)
        template = (self.world_root / PRESET / "charactertemplate.yml").read_text("utf-8")
        self.assertIn("spawn:\n  []", template)
        self.assertIn("could be PROVEN to be on", template)
        self.assertIn(f"See {derive.SPAWN_FILE} `refused:`", template)
        code, _, err = self.cli("check", "TestWorld", "--preset", PRESET)
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
