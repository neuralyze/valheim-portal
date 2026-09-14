#!/usr/bin/env python3
"""Tests for the two halves of the world re-roll path that failed on 2026-09-14:
reading a Valheim 1.0 world at all, and refusing a re-roll BEFORE it stops a server.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("valheim_worldgen.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("valheim_worldgen", MODULE_PATH)
valheim_worldgen = importlib.util.module_from_spec(SPEC)
# Registered before exec, per importlib's own recipe: a dataclass in the module resolves
# its own module out of sys.modules while the class body is being processed.
sys.modules[SPEC.name] = valheim_worldgen
SPEC.loader.exec_module(valheim_worldgen)
import valheim_world  # noqa: E402  (same directory, loaded the way the tool loads it)


# A loaded, generated Valheim 1.0.12 server log, trimmed to the lines that carry the
# facts. Taken verbatim from the live Ulfsland container on 2026-09-14; not one of the
# 0.220 strings the tool used to look for appears anywhere in it.
ONE_ZERO_LOG = """09/14/2026 04:06:05: Get create world TestWorld
09/14/2026 04:06:33: ZNet.LoadWorld: TestWorld (TestWorld), save number 1
09/14/2026 04:06:33: ZoneSystem.Load => DB2: unpacking compressed data 168,044 => 239,021 bytes
09/14/2026 04:06:33: ZoneSystem.Load => Loaded 14,040 locations
"""


def write_metadata(path: Path, name: str, seed: str, world_version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    valheim_world.save(path, {
        "name": name, "seed": seed, "seed_value": valheim_world.stable_hash(seed),
        "uid": 171189434, "world_version": world_version, "generator_version": 2,
        "trailer": b"\x00" * 8,
    })


class WorldFixture(unittest.TestCase):
    """One world directory under a private world root, with no real host behind it."""

    WORLD = "TestWorld"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "worlds"
        self.world_root = self.root / self.WORLD
        self.saves = self.world_root / "config_merged" / "worlds_local"
        self.saves.mkdir(parents=True)
        (self.world_root / "valheim.env").write_text("SERVER_NAME=test\n")
        environment = unittest.mock.patch.dict(os.environ, {"VALHEIM_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def pin(self, seed: str) -> None:
        valheim_worldgen.write_seed_config(self.world_root, seed)

    def status(self, log: str = "", running: bool = True) -> dict:
        """status() with the two docker reads faked; everything else is the real code."""
        def fake_run(command, **_):
            if command[1] == "inspect":
                return SimpleNamespace(stdout="true\n" if running else "false\n", stderr="",
                                       returncode=0)
            return SimpleNamespace(stdout=log, stderr="", returncode=0)

        with unittest.mock.patch.object(valheim_worldgen.subprocess, "run", fake_run):
            return valheim_worldgen.status(self.WORLD)


class StatusReadsBothSaveLayouts(WorldFixture):
    def test_a_valheim_one_world_directory_is_the_save_and_the_backup_beside_it_is_not(self):
        """The live shape: worlds_local holds <World>/ and <World>_backup_auto-<stamp>/.

        Both are directories and both hold a *.fwl2, so "is there a directory" and "is
        there an fwl2 anywhere" both pick the wrong one. The world is the one named for
        the world, and db_bytes must be its bytes alone.
        """
        world = self.saves / self.WORLD
        write_metadata(world / "_main.0.fwl2", self.WORLD, "SeedTest01", 41)
        write_metadata(world / "_main.1.fwl2", self.WORLD, "SeedTest01", 41)
        (world / "_main.1.db2").write_bytes(b"d" * 4096)
        (world / "_main.1.ok").write_bytes(b"\x29\x00\x00\x00")
        backup = self.saves / f"{self.WORLD}_backup_auto-20260914-043557"
        write_metadata(backup / "_main.0.fwl2", self.WORLD, "SeedTest01", 41)
        (backup / "_main.0.db2").write_bytes(b"b" * 999999)
        self.pin("SeedTest01")

        report = self.status(log=ONE_ZERO_LOG)
        self.assertEqual(report["save_format"], "directory")
        self.assertEqual(report["save_path"], str(world))
        self.assertTrue(report["fwl_present"])
        self.assertEqual(report["seed_name"], "SeedTest01")
        self.assertEqual(report["seed"], valheim_world.stable_hash("SeedTest01"))
        self.assertEqual(report["world_version"], 41)
        self.assertTrue(report["seed_matches_pin"])
        # Every file of the save, and nothing from the backup next door.
        self.assertEqual(report["db_bytes"], sum(f.stat().st_size for f in world.iterdir()))

        # With the world gone, the rolling backup must not be promoted to being it.
        for path in sorted(world.iterdir()):
            path.unlink()
        world.rmdir()
        orphan = self.status(log=ONE_ZERO_LOG)
        self.assertIsNone(orphan["save_format"])
        self.assertIsNone(orphan["save_path"])
        self.assertFalse(orphan["fwl_present"])
        self.assertIsNone(orphan["seed_name"])
        self.assertEqual(orphan["db_bytes"], 0)

    def test_a_zero_two_twenty_pair_is_read_as_a_pair_even_beside_an_empty_directory(self):
        """A directory named for the world that holds no *.fwl2 is not a 1.0 world.

        That is the half-created case that hung the 1.0 server in the start scene, and
        "not a pair implies 1.0" would answer "directory" here and then find no seed.
        """
        (self.saves / self.WORLD).mkdir()
        write_metadata(self.saves / f"{self.WORLD}.fwl", self.WORLD, "OldSeed02", 36)
        (self.saves / f"{self.WORLD}.db").write_bytes(b"d" * 365)
        self.pin("OldSeed02")

        report = self.status(log=f"Load world: {self.WORLD}\nDone generating locations\n")
        self.assertEqual(report["save_format"], "pair")
        self.assertEqual(report["save_path"], str(self.saves / f"{self.WORLD}.fwl"))
        self.assertTrue(report["fwl_present"])
        self.assertEqual(report["seed_name"], "OldSeed02")
        self.assertEqual(report["world_version"], 36)
        self.assertTrue(report["seed_matches_pin"])
        self.assertEqual(report["db_bytes"], 365)
        self.assertTrue(report["world_loaded"])
        self.assertTrue(report["locations_generated"])

    def test_the_newest_readable_generation_wins_over_a_truncated_one(self):
        """_main.10 beats _main.9, and a half-written newest generation is skipped
        rather than reported as a world with no seed."""
        world = self.saves / self.WORLD
        write_metadata(world / "_main.9.fwl2", self.WORLD, "SeedTest01", 41)
        write_metadata(world / "_main.10.fwl2", self.WORLD, "SeedTest10", 41)
        report = self.status(log=ONE_ZERO_LOG)
        self.assertEqual(report["seed_name"], "SeedTest10")

        (world / "_main.11.fwl2").write_bytes(b"\x2a\x00\x00\x00truncated")
        report = self.status(log=ONE_ZERO_LOG)
        self.assertEqual(report["save_format"], "directory")
        self.assertEqual(report["seed_name"], "SeedTest10")

    def test_the_valheim_one_log_vocabulary_proves_loaded_and_generated(self):
        """1.0.12 emits neither "Load world: <World>" nor "Done generating locations",
        measured against the live server and against assembly_valheim.dll itself."""
        write_metadata(self.saves / self.WORLD / "_main.0.fwl2", self.WORLD, "SeedTest01", 41)
        report = self.status(log=ONE_ZERO_LOG)
        self.assertTrue(report["world_loaded"])
        self.assertTrue(report["locations_generated"])

        fresh = self.status(log="Loading: Generating locations\n"
                               "Loading: Done. Total seconds sinze ZNet start: 30, "
                               "Genloc duration: 4113 ms, (Total genloc time: 4.1)\n")
        self.assertTrue(fresh["locations_generated"])
        self.assertFalse(self.status(log="Zonesystem Awake 17260\n")["locations_generated"])
        self.assertFalse(self.status(log="Zonesystem Awake 17260\n")["world_loaded"])


class PreconditionsAreCheckedBeforeAnythingIsDisturbed(WorldFixture):
    """The 2026-09-14 incident: the missing-plugin refusal fired after the stop and
    after the archive, leaving the world down with no save in worlds_local."""

    def setUp(self):
        super().setUp()
        write_metadata(self.saves / self.WORLD / "_main.0.fwl2", self.WORLD, "SeedTest01", 41)
        self.hostops = Path(self.temp.name) / "hostops"
        self.hostops.mkdir()
        self.plugin = Path(self.temp.name) / "NeuralyzeWorldSeed.dll"
        self.calls = []
        patches = [
            unittest.mock.patch.object(valheim_worldgen, "HOSTOPS_ROOT", self.hostops),
            unittest.mock.patch.object(valheim_worldgen, "PLUGIN_SOURCE", self.plugin),
            unittest.mock.patch.object(valheim_worldgen, "run_script",
                                       lambda name, world: self.calls.append(name)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def script(self, name: str) -> None:
        path = self.hostops / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)

    def assert_nothing_was_disturbed(self):
        self.assertEqual(self.calls, [], "a lifecycle script ran despite the refusal")
        self.assertEqual(sorted(p.name for p in self.saves.iterdir()), [self.WORLD],
                         "the save was moved despite the refusal")
        self.assertEqual([p.name for p in (self.world_root / "config_merged").iterdir()
                          if p.name.startswith("world-archive-")], [])

    def test_a_missing_seed_plugin_refuses_before_the_server_is_stopped(self):
        self.script("stop_valheim_server.sh")
        self.script("start_valheim_server.sh")
        with self.assertRaises(RuntimeError) as refusal:
            valheim_worldgen.recreate(self.WORLD, "SeedTest02")
        self.assert_nothing_was_disturbed()
        self.assertIn("world seed plugin is missing", str(refusal.exception))
        # The refusal has to say how to satisfy it: the deployment ships no built DLL.
        self.assertIn("build.sh", str(refusal.exception))

    def test_a_missing_start_script_refuses_before_the_server_is_stopped(self):
        """Checking only the script about to run would stop the world and then discover
        it cannot be started again."""
        self.plugin.write_bytes(b"MZ plugin")
        self.script("stop_valheim_server.sh")
        with self.assertRaises(RuntimeError) as refusal:
            valheim_worldgen.recreate(self.WORLD, "SeedTest02")
        self.assert_nothing_was_disturbed()
        self.assertIn("start_valheim_server.sh", str(refusal.exception))

    def test_a_satisfiable_request_stops_archives_and_starts_in_that_order(self):
        self.plugin.write_bytes(b"MZ plugin")
        self.script("stop_valheim_server.sh")
        self.script("start_valheim_server.sh")
        # The check is separable from the action, and touches nothing on its own: that
        # is what makes it safe to run before the stop, and what a dry run can use.
        self.assertEqual(valheim_worldgen.preflight(self.WORLD, "SeedTest02"), self.world_root)
        self.assert_nothing_was_disturbed()
        result = valheim_worldgen.recreate(self.WORLD, "SeedTest02")
        self.assertEqual(self.calls, ["stop_valheim_server.sh", "start_valheim_server.sh"])
        self.assertTrue(result["archived_previous_world_to"].endswith("-worldgen"))
        self.assertEqual(list(self.saves.iterdir()), [])
        archived = self.world_root / "config_merged" / result["archived_previous_world_to"]
        self.assertTrue((archived / self.WORLD / "_main.0.fwl2").is_file())
        self.assertEqual(valheim_worldgen.pinned_seed(self.world_root), "SeedTest02")
        self.assertTrue(valheim_worldgen.plugin_path(self.world_root).is_file())


if __name__ == "__main__":
    unittest.main()
