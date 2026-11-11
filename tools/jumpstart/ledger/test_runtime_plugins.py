"""Temporary DLL/manifest regressions; no live server or world evidence."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import replay, writer


class RuntimePluginFingerprintTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.world = "RuntimeInventoryTest"
        self.deployed = (self.root / "worlds" / self.world / "config_merged"
                         / "bepinex" / "plugins")
        self.runtime = self.root / "server" / "BepInEx" / "plugins"
        self.deployed.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.deployed_bytes = b"world-local plugin, not the running server\x00"
        self.runtime_bytes = b"actual runtime plugin\x00\x01"
        (self.deployed / "WorldOnly.dll").write_bytes(self.deployed_bytes)
        (self.runtime / "nested").mkdir()
        (self.runtime / "nested" / "Runtime.dll").write_bytes(self.runtime_bytes)
        (self.runtime / "ignored.txt").write_bytes(b"not a plugin")

        self.env = self.enterContext(patch.dict(os.environ, {
            "VALHEIM_WORLD_ROOT": str(self.root / "worlds"),
            "VH_WORLD": self.world,
        }))
        self.env.pop("VALHEIM_SERVER_PLUGINS", None)
        # Only the tool identity boundary is replaced. Plugin discovery,
        # byte hashing, manifest creation, and replay comparison are real.
        self.enterContext(patch.object(writer, "tool_fingerprint", return_value={
            "commit": "constructed-test", "dirty": False,
            "schema_version": writer.schema.SCHEMA_VERSION,
        }))

    def fingerprint(self, *, heavy=True):
        return writer.fingerprint(self.world, heavy=heavy, probe_modifiers=False)

    def recorded_manifest(self):
        manifest = self.fingerprint()
        ledger = writer.Ledger.open(
            actor="runtime-plugin-test", root=self.root / "ledger",
            manifest=manifest)
        return ledger.records()[0]["params"]

    def mod_drift(self, recorded, live):
        return [difference for difference in replay.compare_manifest(recorded, live)
                if difference["field"] == "mods"]

    def test_selected_runtime_inventory_is_pinned_and_runtime_change_is_fatal(self):
        self.env["VALHEIM_SERVER_PLUGINS"] = str(self.runtime)
        recorded = self.recorded_manifest()
        self.assertEqual(recorded["mods"]["dlls"], [{
            "path": "nested/Runtime.dll", "bytes": len(self.runtime_bytes),
            "sha256": hashlib.sha256(self.runtime_bytes).hexdigest(),
        }])
        self.assertEqual(self.mod_drift(recorded, self.fingerprint()), [])

        # Same-size replacement must be detected by bytes, not size or name.
        changed_bytes = bytes([self.runtime_bytes[0] ^ 1]) + self.runtime_bytes[1:]
        (self.runtime / "nested" / "Runtime.dll").write_bytes(changed_bytes)
        live = self.fingerprint()
        self.assertNotEqual(recorded["mods"]["digest"], live["mods"]["digest"])
        drift = self.mod_drift(recorded, live)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["level"], "fatal")
        self.assertEqual(drift[0]["changed"], ["nested/Runtime.dll"])

    def test_world_only_dll_changes_do_not_replace_selected_runtime(self):
        self.env["VALHEIM_SERVER_PLUGINS"] = str(self.runtime)
        recorded = self.recorded_manifest()
        (self.deployed / "WorldOnly.dll").write_bytes(b"changed world-only plugin")
        (self.deployed / "Added.dll").write_bytes(b"another world-only plugin")
        live = self.fingerprint()
        self.assertEqual(live["mods"], recorded["mods"])
        self.assertEqual(self.mod_drift(recorded, live), [])

    def test_unset_configuration_keeps_world_local_inventory(self):
        recorded = self.recorded_manifest()
        self.assertEqual(recorded["mods"]["plugins_dir"], str(self.deployed))
        self.assertEqual(recorded["mods"]["dlls"], [{
            "path": "WorldOnly.dll", "bytes": len(self.deployed_bytes),
            "sha256": hashlib.sha256(self.deployed_bytes).hexdigest(),
        }])
        (self.deployed / "WorldOnly.dll").write_bytes(b"deployment changed")
        drift = self.mod_drift(recorded, self.fingerprint())
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["level"], "fatal")

    def test_configuration_is_resolved_on_each_call_and_empty_directory_is_valid(self):
        default = self.fingerprint()
        self.env["VALHEIM_SERVER_PLUGINS"] = str(self.runtime)
        runtime = self.fingerprint()
        self.assertEqual(runtime["mods"]["plugins_dir"], str(self.runtime))
        self.assertNotEqual(runtime["mods"]["digest"], default["mods"]["digest"])

        empty = self.root / "empty-runtime-plugins"
        empty.mkdir()
        self.env["VALHEIM_SERVER_PLUGINS"] = str(empty)
        empty_manifest = self.fingerprint()
        self.assertTrue(empty_manifest["mods"]["measured"])
        self.assertEqual(self.mod_drift(runtime, empty_manifest)[0]["removed"],
                         ["nested/Runtime.dll"])
        self.env.pop("VALHEIM_SERVER_PLUGINS")
        self.assertEqual(self.fingerprint()["mods"], default["mods"])

    def test_bad_explicit_paths_refuse_instead_of_falling_back(self):
        not_directory = self.root / "plugin-file"
        not_directory.write_bytes(b"not a directory")
        for configured in ("", "relative/plugins", str(self.root / "missing"),
                           str(not_directory)):
            for heavy in (True, False):
                with self.subTest(configured=configured, heavy=heavy):
                    self.env["VALHEIM_SERVER_PLUGINS"] = configured
                    with self.assertRaises(writer.LedgerError):
                        self.fingerprint(heavy=heavy)

    def test_light_manifest_does_not_read_dll_bytes_and_cannot_assert_no_drift(self):
        self.env["VALHEIM_SERVER_PLUGINS"] = str(self.runtime)
        measured = self.fingerprint()
        with patch.object(Path, "open", side_effect=AssertionError(
                "light fingerprint must not open DLL bytes")):
            light = self.fingerprint(heavy=False)
        self.assertFalse(light["mods"]["measured"])
        # JSON round trip exercises the same light manifest representation
        # persisted in a ledger, without changing its unmeasured status.
        drift = self.mod_drift(json.loads(json.dumps(light)), measured)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["level"], "fatal")


if __name__ == "__main__":
    unittest.main()
