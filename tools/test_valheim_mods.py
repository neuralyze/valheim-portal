#!/usr/bin/env python3
import argparse
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("valheim_mods.py")
SPEC = importlib.util.spec_from_file_location("valheim_mods", MODULE_PATH)
valheim_mods = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(valheim_mods)


class DeployTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = Path(self.temp.name) / "TestWorld"
        self.root = self.world / "mods/profiles/test-profile"
        server = self.root / "manager-cache/server/BepInEx/plugins/NewPlugin"
        server.mkdir(parents=True)
        (server / "new.dll").write_text("new")
        (self.root / "manual-mods").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def deploy(self):
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            valheim_mods.cmd_deploy(self.root, {"world_name": "TestWorld"}, SimpleNamespace(apply=True))
        finally:
            valheim_mods.require_stopped = original

    def test_moves_legacy_backup_outside_bepinex_root(self):
        target = self.world / "config_merged/bepinex/plugins/OldPlugin"
        legacy = self.world / "config_merged/bepinex/plugins.previous/More_World_Traders"
        target.mkdir(parents=True)
        legacy.mkdir(parents=True)
        (target / "old.dll").write_text("old")
        (legacy / "More_World_Traders.dll").write_text("legacy")

        self.deploy()

        bepinex = self.world / "config_merged/bepinex"
        backup = self.world / "mods/deployment-backups/test-profile"
        self.assertTrue((bepinex / "plugins/NewPlugin/new.dll").is_file())
        self.assertFalse((bepinex / "plugins.previous").exists())
        self.assertTrue((backup / "server-plugins.previous/OldPlugin/old.dll").is_file())
        self.assertTrue((backup / "legacy-plugins.previous/More_World_Traders/More_World_Traders.dll").is_file())

    def test_rejects_cache_version_that_differs_from_profile_manifest(self):
        target_parent = self.world / "config_merged/bepinex"
        target_parent.mkdir(parents=True)
        plugin = self.root / "manager-cache/server/BepInEx/plugins/BetterRiding"
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text(json.dumps({
            "name": "BetterRiding",
            "version_number": "1.3.2",
        }))
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            with self.assertRaisesRegex(RuntimeError, "does not match profile manifest"):
                valheim_mods.cmd_deploy(
                    self.root,
                    {"world_name": "TestWorld", "packages": [{"identifier": "Yggdrah-BetterRiding", "version": "1.3.5"}]},
                    SimpleNamespace(apply=True),
                )
        finally:
            valheim_mods.require_stopped = original
        self.assertFalse((target_parent / "plugins").exists())

    def test_accepts_utf8_bom_in_cached_package_manifest(self):
        target_parent = self.world / "config_merged/bepinex"
        target_parent.mkdir(parents=True)
        plugin = self.root / "manager-cache/server/BepInEx/plugins/NewPlugin"
        (plugin / "manifest.json").write_text(
            "\ufeff" + json.dumps({"name": "NewPlugin", "version_number": "1.2.3"}),
            encoding="utf-8",
        )
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            valheim_mods.cmd_deploy(
                self.root,
                {"world_name": "TestWorld", "packages": [{"identifier": "Owner-NewPlugin", "version": "1.2.3"}]},
                SimpleNamespace(apply=True),
            )
        finally:
            valheim_mods.require_stopped = original
        self.assertTrue((target_parent / "plugins/NewPlugin/new.dll").is_file())

    def test_refuses_deploy_until_client_release_cutover_is_confirmed(self):
        (self.world / "config_merged/bepinex").mkdir(parents=True)
        marker = self.world / "mods/.client-release-cutover.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({
            "schema_version": 1,
            "world_name": "TestWorld",
            "removals": [{
                "identifier": "Yggdrah-BetterRiding",
                "source_profile": "test-profile",
                "targets": [{"profile": "test-vr", "client_type": "vr"}],
            }],
            "confirmations": {},
        }))
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            with self.assertRaisesRegex(RuntimeError, "cutover is incomplete"):
                valheim_mods.cmd_deploy(self.root, {"world_name": "TestWorld"}, SimpleNamespace(apply=True))
        finally:
            valheim_mods.require_stopped = original



class RemoveTest(unittest.TestCase):
    identifier = "warpalicious-More_World_Traders"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = Path(self.temp.name) / "TestWorld"
        self.root = self.world / "mods/profiles/test-profile"
        self.manifest = self.root / "profile-manifest.json"
        self.data = {
            "world_name": "TestWorld",
            "packages": [{"identifier": self.identifier, "version": "1.0.11"}],
            "client_only_packages": [],
            "disabled_packages": [],
            "excluded_packages": [{"identifier": self.identifier, "version": "1.0.11", "reason": "old"}],
            "manual_server_packages": [],
            "custom_packages": [],
        }
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps(self.data))
        for side in ("client", "server"):
            plugin = self.root / f"manager-cache/{side}/BepInEx/plugins/More_World_Traders"
            plugin.mkdir(parents=True)
            (plugin / "More_World_Traders.dll").write_text("plugin")
        deployed = self.world / "config_merged/bepinex/plugins/More_World_Traders"
        deployed.mkdir(parents=True)
        (deployed / "More_World_Traders.dll").write_text("plugin")
        config = self.world / "config_merged/bepinex/warpalicious.More_World_Traders.cfg"
        config.write_text("## Plugin GUID: warpalicious.More_World_Traders\n")
        (config.with_name("unrelated.cfg")).write_text("## Plugin GUID: unrelated.Plugin\n")
        # The real client_release_targets reads portal/release-targets.json, which is
        # untracked operator data and therefore absent from a fresh clone. Stubbing it
        # here keeps every remove test hermetic instead of silently depending on
        # whichever catalog happens to sit beside the checkout.
        original_targets = valheim_mods.client_release_targets
        valheim_mods.client_release_targets = lambda *_: self.release_targets()
        self.addCleanup(setattr, valheim_mods, "client_release_targets", original_targets)

    def release_targets(self):
        return []

    def tearDown(self):
        self.temp.cleanup()

    def remove(self):
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            valheim_mods.cmd_remove(
                self.root,
                self.data,
                SimpleNamespace(identifier=self.identifier, reason="obsolete", manifest=self.manifest),
            )
        finally:
            valheim_mods.require_stopped = original

    def test_removes_manifest_files_and_plugin_config(self):
        self.remove()
        manifest = json.loads(self.manifest.read_text())
        self.assertFalse(valheim_mods.matching_manifest_entries(manifest, self.identifier))
        self.assertFalse(valheim_mods.package_paths(self.root, self.identifier))
        self.assertFalse(valheim_mods.plugin_config_files(self.world, self.identifier))
        self.assertTrue((self.world / "config_merged/bepinex/unrelated.cfg").is_file())
        backups = list((self.world / "mods/removal-backups/test-profile").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "profile-manifest.json").is_file())
        self.assertTrue((backups[0] / "removal.json").is_file())

    def test_refuses_removal_while_server_is_running(self):
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: (_ for _ in ()).throw(RuntimeError("TestWorld is running"))
        try:
            with self.assertRaisesRegex(RuntimeError, "running"):
                valheim_mods.cmd_remove(
                    self.root,
                    self.data,
                    SimpleNamespace(identifier=self.identifier, reason="obsolete", manifest=self.manifest),
                )
        finally:
            valheim_mods.require_stopped = original
        self.assertTrue(valheim_mods.matching_manifest_entries(self.data, self.identifier))

    def test_purge_removes_orphaned_package_without_readding_it(self):
        self.data["packages"] = []
        self.data["excluded_packages"] = []
        self.manifest.write_text(json.dumps(self.data))
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            valheim_mods.cmd_purge(
                self.root,
                self.data,
                SimpleNamespace(identifier=self.identifier, reason="orphaned", manifest=self.manifest),
            )
        finally:
            valheim_mods.require_stopped = original
        self.assertFalse(valheim_mods.package_paths(self.root, self.identifier))
        self.assertFalse(valheim_mods.plugin_config_files(self.world, self.identifier))
        backups = list((self.world / "mods/removal-backups/test-profile").iterdir())
        removal = json.loads((backups[0] / "removal.json").read_text())
        self.assertEqual(removal["operation"], "purge")



class ClientReleaseCutoverTest(RemoveTest):
    def setUp(self):
        super().setUp()
        self.targets = [
            {"profile": "test-flat", "client_type": "flat"},
            {"profile": "test-vr", "client_type": "vr"},
        ]

    def remove_with_cutover(self):
        original_stopped = valheim_mods.require_stopped
        original_targets = valheim_mods.client_release_targets
        valheim_mods.require_stopped = lambda _: None
        valheim_mods.client_release_targets = lambda *_: self.targets
        try:
            valheim_mods.cmd_remove(
                self.root,
                self.data,
                SimpleNamespace(identifier=self.identifier, reason="obsolete", manifest=self.manifest),
            )
        finally:
            valheim_mods.require_stopped = original_stopped
            valheim_mods.client_release_targets = original_targets

    def release_archive(self, profile, client_type, includes_removed=False):
        archive = self.world / f"{profile}.zip"
        packages = [{"identifier": self.identifier}] if includes_removed else []
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("profile-manifest.json", json.dumps({
                "world": "TestWorld",
                "profile": profile,
                "client_type": client_type,
                "packages": packages,
                "client_only_packages": [],
            }))
        return archive

    def confirm(self, profile, client_type, archive):
        valheim_mods.cmd_release_confirm(
            self.root,
            self.data,
            SimpleNamespace(
                profile_name=profile,
                client_type=client_type,
                release_id=f"{profile}-2.0.0",
                archive=archive,
            ),
        )

    def test_removal_requires_every_mapped_client_release_confirmation(self):
        self.remove_with_cutover()
        marker = valheim_mods.release_cutover_path(self.world)
        self.assertTrue(marker.is_file())
        with self.assertRaisesRegex(RuntimeError, "cutover is incomplete"):
            valheim_mods.require_release_cutover_complete(self.world)

        with self.assertRaisesRegex(RuntimeError, "still selects"):
            self.confirm("test-flat", "flat", self.release_archive("test-flat", "flat", True))
        self.confirm("test-flat", "flat", self.release_archive("test-flat", "flat"))
        self.assertTrue(marker.is_file())
        self.confirm("test-vr", "vr", self.release_archive("test-vr", "vr"))
        self.assertFalse(marker.exists())
        valheim_mods.require_release_cutover_complete(self.world)

class DependencyConflictTest(unittest.TestCase):
    # Thunderstore dependency strings are minimums: a profile that already selects
    # a newer build satisfies the pin, only an older selection is a conflict.
    registry = {
        "Yggdrah-BetterRiding": {
            "full_name": "Yggdrah-BetterRiding",
            "name": "BetterRiding",
            "categories": ["Server-side"],
            "versions": [
                {"version_number": "1.3.2", "dependencies": []},
                {"version_number": "1.3.5", "dependencies": []},
                {"version_number": "1.3.6", "dependencies": []},
            ],
        },
        "Yggdrah-DragonRiders": {
            "full_name": "Yggdrah-DragonRiders",
            "name": "DragonRiders",
            "categories": ["Server-side"],
            "versions": [{"version_number": "2.1.6", "dependencies": ["Yggdrah-BetterRiding-1.3.5"]}],
        },
    }

    def add_dragon_riders(self, selected_version):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        manifest_path = root / "profile-manifest.json"
        manifest = {
            "world_name": "TestWorld",
            "packages": [{"identifier": "Yggdrah-BetterRiding", "version": selected_version}],
            "client_only_packages": [],
        }
        manifest_path.write_text(json.dumps(manifest))
        installs = []
        original_index = valheim_mods.index
        original_install = valheim_mods.install
        valheim_mods.index = lambda: self.registry
        valheim_mods.install = lambda *args: installs.append((args[1]["full_name"], args[2], args[3]))
        self.addCleanup(setattr, valheim_mods, "index", original_index)
        self.addCleanup(setattr, valheim_mods, "install", original_install)
        call = lambda: valheim_mods.cmd_add(
            root,
            manifest,
            SimpleNamespace(
                identifier="Yggdrah-DragonRiders",
                version="2.1.6",
                client_only=False,
                manifest=manifest_path,
            ),
        )
        return manifest, installs, call

    def test_conflicting_dependency_does_not_mutate_cache_or_manifest(self):
        manifest, installs, add = self.add_dragon_riders("1.3.2")
        with self.assertRaisesRegex(RuntimeError, "Dependency conflict"):
            add()
        self.assertEqual(installs, [])
        self.assertEqual(manifest["packages"], [{"identifier": "Yggdrah-BetterRiding", "version": "1.3.2"}])

    def test_newer_selected_dependency_satisfies_the_pin(self):
        manifest, installs, add = self.add_dragon_riders("1.3.6")
        add()
        self.assertEqual(
            manifest["packages"],
            [
                {"identifier": "Yggdrah-BetterRiding", "version": "1.3.6"},
                {"identifier": "Yggdrah-DragonRiders", "version": "2.1.6", "scope": "shared"},
            ],
        )
        self.assertEqual(
            installs,
            [("Yggdrah-DragonRiders", "2.1.6", "client"), ("Yggdrah-DragonRiders", "2.1.6", "server")],
        )


class DispatchTest(unittest.TestCase):
    def subparser_names(self):
        parser = valheim_mods.build_parser()
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        return set(action.choices)

    def test_every_registered_subcommand_has_a_handler(self):
        self.assertEqual(self.subparser_names(), set(valheim_mods.COMMANDS))

    def test_unwired_subcommand_reports_the_gap_instead_of_raising_keyerror(self):
        handler = valheim_mods.COMMANDS.pop("custom-add")
        self.addCleanup(valheim_mods.COMMANDS.__setitem__, "custom-add", handler)
        argv = sys.argv
        sys.argv = ["valheim_mods.py", "--manifest", "missing/profile-manifest.json", "custom-add", "pack.zip"]
        self.addCleanup(setattr, sys, "argv", argv)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = valheim_mods.main()
        self.assertEqual(status, 2)
        self.assertIn("custom-add", stderr.getvalue())


class ExportCodeTest(unittest.TestCase):
    def test_missing_exporter_reports_expected_path(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        with self.assertRaises(RuntimeError) as error:
            valheim_mods.cmd_export(root, {}, SimpleNamespace())
        message = str(error.exception)
        self.assertIn(str(root / "export_profile_code.py"), message)
        self.assertIn("not bundled", message)


if __name__ == "__main__":
    unittest.main()
