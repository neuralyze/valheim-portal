#!/usr/bin/env python3
import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
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
        self.fleet = Path(self.temp.name)
        self.world = self.fleet / "TestWorld"
        (self.world / "mods").mkdir(parents=True)
        self.root = self.fleet / "profiles/test-profile"
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
            valheim_mods.cmd_deploy(self.root, {}, SimpleNamespace(apply=True, world_dir=self.world))
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
                    {"packages": [{"identifier": "Yggdrah-BetterRiding", "version": "1.3.5"}]},
                    SimpleNamespace(apply=True, world_dir=self.world),
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
                {"packages": [{"identifier": "Owner-NewPlugin", "version": "1.2.3"}]},
                SimpleNamespace(apply=True, world_dir=self.world),
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
                valheim_mods.cmd_deploy(self.root, {}, SimpleNamespace(apply=True, world_dir=self.world))
        finally:
            valheim_mods.require_stopped = original



class RemoveTest(unittest.TestCase):
    identifier = "warpalicious-More_World_Traders"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fleet = Path(self.temp.name)
        self.world = self.fleet / "TestWorld"
        self.root = self.fleet / "profiles/test-profile"
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
        (self.world / "mods").mkdir(parents=True)
        (self.world / "mods/.active-mod-profile").write_text("test-profile\n")
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
                SimpleNamespace(identifier=self.identifier, reason="obsolete", manifest=self.manifest, world_dir=self.world),
            )
        finally:
            valheim_mods.require_stopped = original

    def test_removes_manifest_files_and_plugin_config(self):
        self.remove()
        manifest = json.loads(self.manifest.read_text())
        self.assertFalse(valheim_mods.matching_manifest_entries(manifest, self.identifier))
        self.assertFalse(valheim_mods.package_paths(self.root, self.world, self.identifier))
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
                    SimpleNamespace(identifier=self.identifier, reason="obsolete", manifest=self.manifest, world_dir=self.world),
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
                SimpleNamespace(identifier=self.identifier, reason="orphaned", manifest=self.manifest, world_dir=self.world),
            )
        finally:
            valheim_mods.require_stopped = original
        self.assertFalse(valheim_mods.package_paths(self.root, self.world, self.identifier))
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
                SimpleNamespace(identifier=self.identifier, reason="obsolete", manifest=self.manifest, world_dir=self.world),
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
                world_dir=self.world,
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


class ServerConfigDeployTest(unittest.TestCase):
    """Deploy places the profile's server settings, with this server's overrides applied.

    Before this, the plugins wrote their own settings on each server's first run, so one
    shared mod set could be configured four different ways with nothing recording it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fleet = Path(self.temp.name)
        self.world = self.fleet / "TestWorld"
        self.root = self.fleet / "profiles/test-profile"
        server = self.root / "manager-cache/server/BepInEx/plugins/NewPlugin"
        server.mkdir(parents=True)
        (server / "new.dll").write_text("new")
        (self.root / "manual-mods").mkdir()
        (self.world / "mods").mkdir(parents=True)
        (self.world / "config_merged/bepinex").mkdir(parents=True)
        self.declared = self.root / "server-config"
        self.declared.mkdir()
        (self.declared / "Azumatt.WardIsLove.cfg").write_text(
            "## Plugin GUID: Azumatt.WardIsLove\n[General]\nWardHotKey = F4\nShowMarker = true\n")

    def deploy(self):
        original = valheim_mods.require_stopped
        valheim_mods.require_stopped = lambda _: None
        try:
            valheim_mods.cmd_deploy(self.root, {}, SimpleNamespace(apply=True, world_dir=self.world))
        finally:
            valheim_mods.require_stopped = original

    def test_profile_settings_reach_the_server(self):
        self.deploy()

        live = (self.world / "config_merged/bepinex/Azumatt.WardIsLove.cfg").read_text()
        self.assertIn("WardHotKey = F4", live)
        self.assertIn("ShowMarker = true", live)

    def test_a_server_override_replaces_only_its_own_key(self):
        overrides = self.world / "mods/overrides/server"
        overrides.mkdir(parents=True)
        (overrides / "Azumatt.WardIsLove.cfg").write_text("[General]\nWardHotKey = F7\n")

        self.deploy()

        live = (self.world / "config_merged/bepinex/Azumatt.WardIsLove.cfg").read_text()
        self.assertIn("WardHotKey = F7", live)      # the override
        self.assertIn("ShowMarker = true", live)    # still the profile's value
        self.assertIn("## Plugin GUID: Azumatt.WardIsLove", live)

    def test_the_previous_settings_are_kept_before_the_profile_claims_them(self):
        # A plugin's own file is the only record of what a server ran before.
        live = self.world / "config_merged/bepinex/Azumatt.WardIsLove.cfg"
        live.write_text("[General]\nWardHotKey = G\n")

        self.deploy()

        kept = self.world / "mods/deployment-backups/test-profile/server-config.previous/Azumatt.WardIsLove.cfg"
        self.assertTrue(kept.is_file())
        self.assertIn("WardHotKey = G", kept.read_text())

    def test_a_profile_that_declares_no_server_settings_changes_nothing(self):
        shutil.rmtree(self.declared)
        live = self.world / "config_merged/bepinex/Azumatt.WardIsLove.cfg"
        live.write_text("[General]\nWardHotKey = G\n")

        self.deploy()

        self.assertEqual(live.read_text(), "[General]\nWardHotKey = G\n")


class SettingsHistoryWiringTest(RemoveTest):
    """main() records settings around every mutating command.

    Inherits RemoveTest's world because that fixture already has the three places
    settings live: the manifest, the profile's configs, and the server's cfg tree.
    """

    def setUp(self):
        super().setUp()
        self.store = Path(self.temp.name) / "settings-history"
        os.environ["VALHEIM_SETTINGS_HISTORY"] = str(self.store)
        self.addCleanup(os.environ.pop, "VALHEIM_SETTINGS_HISTORY", None)

    def run_main(self, *arguments):
        os.environ["VALHEIM_ROOT"] = str(self.fleet)
        self.addCleanup(os.environ.pop, "VALHEIM_ROOT", None)
        argv = sys.argv
        sys.argv = ["valheim_mods.py", "--world", "TestWorld", *arguments]
        self.addCleanup(setattr, sys, "argv", argv)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = valheim_mods.main()
        return status, out.getvalue(), err.getvalue()

    def commit_subjects(self):
        return subprocess.run(["git", "-C", str(self.store), "log", "--format=%s"],
                              capture_output=True, text=True, check=True).stdout.split("\n")

    def test_a_manifest_edit_lands_in_history(self):
        # exclude is the one mutating command that needs neither the network nor
        # a container, so it exercises the wiring rather than a mod download.
        status, out, _ = self.run_main("exclude", "some-Mod", "1.0", "--reason", "test")

        self.assertEqual(status, 0)
        self.assertIn("settings_history=", out)
        subjects = self.commit_subjects()
        self.assertIn("TestWorld: exclude some-Mod 1.0 --reason test", subjects)
        # The pre-work snapshot is what survives a crash inside the handler.
        self.assertIn("TestWorld: before exclude some-Mod 1.0 --reason test", subjects)
        recorded = self.store / "profiles/test-profile/profile-manifest.json"
        self.assertIn("some-Mod", recorded.read_text())

    def test_a_read_only_command_records_nothing(self):
        status, _, _ = self.run_main("list")

        self.assertEqual(status, 0)
        self.assertFalse(self.store.exists())

    def test_a_removal_stops_when_history_cannot_be_written(self):
        # A file where the store must go: git init cannot succeed, so the only
        # copy of these configs would be the removal backup alone.
        blocked = Path(self.temp.name) / "blocked"
        blocked.write_text("not a directory")
        os.environ["VALHEIM_SETTINGS_HISTORY"] = str(blocked / "store")
        config = self.world / "config_merged/bepinex/warpalicious.More_World_Traders.cfg"

        status, _, err = self.run_main("remove", self.identifier, "--reason", "obsolete")

        self.assertEqual(status, 2)
        self.assertIn("history", err)
        # Refused before the work: the mod and its config are untouched.
        self.assertTrue(config.is_file())
        self.assertTrue(valheim_mods.matching_manifest_entries(
            json.loads(self.manifest.read_text()), self.identifier))


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


def test_crossed_versions_spans_every_release_between():
    """A 0.9.5 -> 0.12.0 jump crosses eight releases, and the note that matters is often not the
    newest one. Reporting only the target version is how a save-data change goes unread."""
    package = {'versions': [{'version_number': v} for v in
                            ('0.9.5', '0.10.0', '0.11.0', '0.11.1', '0.11.2', '0.11.3', '0.12.0', '0.8.0')]}
    got = valheim_mods.crossed_versions(package, '0.9.5', '0.12.0')
    assert got == ['0.10.0', '0.11.0', '0.11.1', '0.11.2', '0.11.3', '0.12.0']
    assert '0.9.5' not in got and '0.8.0' not in got


def test_github_repository_reads_the_owner_and_repo():
    assert valheim_mods.github_repository({'website_url': 'https://github.com/Author/SomeMod'}) == ('Author', 'SomeMod')
    assert valheim_mods.github_repository({'website_url': 'https://github.com/Author/SomeMod.git/'}) == ('Author', 'SomeMod')
    # A Thunderstore page is not a source repository, and neither is a bare profile link.
    assert valheim_mods.github_repository({'website_url': 'https://thunderstore.io/c/valheim/p/Author/SomeMod/'}) is None
    assert valheim_mods.github_repository({'website_url': 'https://github.com/Author'}) is None
    assert valheim_mods.github_repository({}) is None


class RemovalRecordSurvivesFailureTest(RemoveTest):
    """A failure after the manifest is rewritten must still leave a record saying so.

    The three worlds whose OdinHorse removal aborted in the cutover step were left with a
    backup directory holding no removal.json, so the next run answered 'Not present' and read
    as though the removal had never started."""

    def test_record_names_the_failure_and_the_error_names_the_backup(self):
        original = valheim_mods.record_release_cutover
        valheim_mods.record_release_cutover = lambda *_: (_ for _ in ()).throw(
            RuntimeError("Invalid client release cutover record: /w/mods/.client-release-cutover.json"))
        self.addCleanup(setattr, valheim_mods, "record_release_cutover", original)
        with self.assertRaisesRegex(RuntimeError, "already rewritten"):
            self.remove()
        backups = list((self.world / "mods/removal-backups/test-profile").iterdir())
        record = json.loads((backups[0] / "removal.json").read_text())
        self.assertEqual(record["state"], "failed")
        self.assertIn("Invalid client release cutover record", record["failure"])
        self.assertEqual(record["identifier"], self.identifier)
        # The manifest really was rewritten, which is what makes a silent record dangerous.
        self.assertFalse(valheim_mods.matching_manifest_entries(
            json.loads(self.manifest.read_text()), self.identifier))

    def test_completed_removal_records_its_state(self):
        self.remove()
        backups = list((self.world / "mods/removal-backups/test-profile").iterdir())
        record = json.loads((backups[0] / "removal.json").read_text())
        self.assertEqual(record["state"], "completed")
        self.assertIn("removed_at", record)


class PlayerCatalogTest(unittest.TestCase):
    """The player-visible mod list: who is on it, who is not, and why.

    Every fixture identifier below is real and installed on this fleet, because the rules being
    tested are judgements about specific packages and a synthetic name proves nothing about them.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fleet = Path(self.temp.name)
        self.world = self.fleet / "Hrafnheim"
        (self.world / "config_merged/bepinex/plugins").mkdir(parents=True)
        self.store = self.fleet / "profiles"
        self.registry = {}
        original = valheim_mods.index
        valheim_mods.index = lambda: self.registry
        self.addCleanup(setattr, valheim_mods, "index", original)

    def tearDown(self):
        self.temp.cleanup()

    def profile(self, name, packages, client_only=()):
        path = self.store / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "profile-manifest.json").write_text(json.dumps({
            "profile_name": name,
            "schema_version": 3,
            "packages": [{"identifier": i, "version": v, "scope": "shared"} for i, v in packages],
            # Real manifests carry entries with no scope key at all, so one fixture entry omits it.
            "client_only_packages": [{"identifier": i, "version": v} for i, v in client_only],
            "disabled_packages": [],
            "custom_packages": [],
            # Mixed objects and bare strings, exactly as a live manifest carries them.
            "excluded_packages": [{"identifier": "Some-Thing", "version": "1.0.0", "reason": "x"}, "Other-Thing"],
        }) + "\n")

    def package(self, identifier, categories, description="a description", version="1.0.0"):
        name = valheim_mods.package_install_name(identifier)
        self.registry[identifier] = {
            "full_name": identifier, "name": name, "owner": identifier.partition("-")[0],
            "categories": list(categories), "package_url": f"https://thunderstore.io/p/{name}/",
            "versions": [{"version_number": version, "description": description, "downloads": 1}],
        }

    def plugin_manifest(self, plugin_name, body, bom=False):
        directory = self.world / "config_merged/bepinex/plugins" / plugin_name
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(body).encode()
        (directory / "manifest.json").write_bytes(b"\xef\xbb\xbf" + payload if bom else payload)

    def catalog(self):
        return valheim_mods.player_catalog(self.world, self.store)

    def listed(self):
        return [mod["identifier"] for mod in self.catalog()["mods"]]

    def test_a_package_only_the_admin_edition_installs_never_appears(self):
        # Not because it is named anywhere: the union of the player editions simply does not
        # contain it. Tristan-ValheimRcon is one of the 6 that are the admin profile's alone.
        self.profile("vr", [("Advize-PlantEasily", "2.1.1")])
        self.profile("flat", [("Advize-PlantEasily", "2.1.1")])
        self.profile("admin", [("Advize-PlantEasily", "2.1.1"), ("Tristan-ValheimRcon", "1.4.0")])
        self.package("Advize-PlantEasily", ["Mods"])
        self.package("Tristan-ValheimRcon", ["Server-side"])

        self.assertEqual(self.listed(), ["Advize-PlantEasily"])

    def test_an_admin_tool_in_both_player_editions_is_excluded_by_name_with_a_reason(self):
        # The set difference is necessary but not sufficient: these four ship to both player
        # editions, so nothing structural is left to filter them on and each carries its reason.
        admin_tools = [
            "JereKuusela-Server_devcommands", "JereKuusela-World_Edit_Commands",
            "JereKuusela-Infinity_Hammer", "Neobotics-RuinsMaker",
        ]
        installed = [(identifier, "1.0.0") for identifier in admin_tools]
        self.profile("vr", [("Advize-PlantEasily", "2.1.1")], client_only=installed)
        self.profile("flat", [("Advize-PlantEasily", "2.1.1")], client_only=installed)
        self.package("Advize-PlantEasily", ["Mods"])
        for identifier in admin_tools:
            self.package(identifier, ["Tools"])

        self.assertEqual(self.listed(), ["Advize-PlantEasily"])
        for identifier in admin_tools:
            self.assertIn(identifier, valheim_mods.PLAYER_IRRELEVANT)
            self.assertTrue(valheim_mods.PLAYER_IRRELEVANT[identifier].strip(),
                            f"{identifier} is excluded with no reason recorded")

    def test_a_library_never_appears_but_a_mod_that_also_exposes_an_api_does(self):
        # The bare "categories contains Libraries" rule deleted Backpacks and
        # CreatureLevelAndLootControl, two of the most visible mods on the server, because their
        # authors also publish an API. The conjunction with a content category is what saves them.
        self.profile("vr", [
            ("ValheimModding-Jotunn", "2.24.3"),
            ("Smoothbrain-Backpacks", "1.3.9"),
            ("Smoothbrain-CreatureLevelAndLootControl", "13.2.2"),
        ])
        self.profile("flat", [("ValheimModding-Jotunn", "2.24.3")])
        self.package("ValheimModding-Jotunn", ["Libraries"])
        self.package("Smoothbrain-Backpacks", ["Gear", "Libraries"])
        self.package("Smoothbrain-CreatureLevelAndLootControl", ["Enemies", "Libraries"])

        self.assertEqual(sorted(self.listed()),
                         ["Smoothbrain-Backpacks", "Smoothbrain-CreatureLevelAndLootControl"])

    def test_the_library_tag_is_not_shown_to_a_player_on_a_mod_that_is_kept(self):
        self.profile("vr", [("Smoothbrain-Backpacks", "1.3.9")])
        self.profile("flat", [("Smoothbrain-Backpacks", "1.3.9")])
        self.package("Smoothbrain-Backpacks", ["Gear", "Libraries"])

        self.assertEqual(self.catalog()["mods"][0]["categories"], ["Gear"])

    def test_a_mod_whose_manifest_carries_a_utf8_bom_still_appears(self):
        # 26 of the 100 manifests shipped under this world begin EF BB BF, and strict utf-8 json
        # fails all 26 with "Unexpected UTF-8 BOM". On 2026-08-20 that made an installed mod read
        # as absent. blacks7ar-CoreWoodPieces is one of the 26.
        self.profile("vr", [("blacks7ar-CoreWoodPieces", "1.1.2")])
        self.profile("flat", [("blacks7ar-CoreWoodPieces", "1.1.2")])
        self.plugin_manifest("CoreWoodPieces", {
            "name": "CoreWoodPieces", "version_number": "1.1.2",
            "description": "Adds core wood building pieces.",
        }, bom=True)

        catalog = self.catalog()
        self.assertEqual([mod["identifier"] for mod in catalog["mods"]], ["blacks7ar-CoreWoodPieces"])
        entry = catalog["mods"][0]
        self.assertEqual(entry["description"], "Adds core wood building pieces.")
        self.assertEqual(entry["source"], "plugin-manifest")

    def test_an_unreadable_manifest_leaves_the_mod_listed_with_unknown_metadata(self):
        # A parse failure must degrade to "we do not know", never to "it is not installed".
        self.profile("vr", [("blacks7ar-CoreWoodPieces", "1.1.2")])
        self.profile("flat", [("blacks7ar-CoreWoodPieces", "1.1.2")])
        directory = self.world / "config_merged/bepinex/plugins/CoreWoodPieces"
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_bytes(b"{ this is not json")

        entry = self.catalog()["mods"][0]
        self.assertEqual(entry["identifier"], "blacks7ar-CoreWoodPieces")
        self.assertEqual(entry["name"], "blacks7ar-CoreWoodPieces")
        self.assertEqual(entry["description"], "")
        self.assertEqual(entry["source"], "unknown")

    def test_a_thunderstore_outage_lists_every_mod_and_says_the_metadata_is_incomplete(self):
        self.profile("vr", [("blacks7ar-CoreWoodPieces", "1.1.2")])
        self.profile("flat", [("blacks7ar-CoreWoodPieces", "1.1.2")])
        self.plugin_manifest("CoreWoodPieces", {"name": "CoreWoodPieces", "description": "Core wood pieces."})
        valheim_mods.index = lambda: (_ for _ in ()).throw(valheim_mods.requests.RequestException("no network"))

        catalog = self.catalog()
        self.assertFalse(catalog["metadata_complete"])
        self.assertEqual([mod["identifier"] for mod in catalog["mods"]], ["blacks7ar-CoreWoodPieces"])

    def test_the_description_comes_from_the_installed_version_not_the_newest(self):
        self.profile("vr", [("Advize-PlantEasily", "2.1.1")])
        self.profile("flat", [("Advize-PlantEasily", "2.1.1")])
        self.package("Advize-PlantEasily", ["Mods"], description="the installed one", version="2.1.1")
        self.registry["Advize-PlantEasily"]["versions"].insert(
            0, {"version_number": "2.2.0", "description": "a release nobody here runs", "downloads": 1})

        self.assertEqual(self.catalog()["mods"][0]["description"], "the installed one")

    def test_the_fingerprint_moves_when_the_installed_set_moves(self):
        # This is the staleness check the portal reads on every page view, so it has to react to a
        # version bump and to an addition, and stay put when nothing changed.
        self.profile("vr", [("Advize-PlantEasily", "2.1.1")])
        self.profile("flat", [("Advize-PlantEasily", "2.1.1")])
        before = valheim_mods.catalog_fingerprint(valheim_mods.player_package_versions(self.store))
        self.assertEqual(before, valheim_mods.catalog_fingerprint(valheim_mods.player_package_versions(self.store)))

        self.profile("vr", [("Advize-PlantEasily", "2.1.2")])
        bumped = valheim_mods.catalog_fingerprint(valheim_mods.player_package_versions(self.store))
        self.assertNotEqual(before, bumped)

        self.profile("vr", [("Advize-PlantEasily", "2.1.2"), ("Azumatt-FastLink", "1.0.4")])
        self.assertNotEqual(bumped, valheim_mods.catalog_fingerprint(valheim_mods.player_package_versions(self.store)))

    def test_the_fingerprint_ignores_a_change_to_the_admin_edition_alone(self):
        # An admin tool added to the admin profile cannot change what a player sees, so it must not
        # force a rebuild either.
        self.profile("vr", [("Advize-PlantEasily", "2.1.1")])
        self.profile("flat", [("Advize-PlantEasily", "2.1.1")])
        self.profile("admin", [("Advize-PlantEasily", "2.1.1")])
        before = valheim_mods.catalog_fingerprint(valheim_mods.player_package_versions(self.store))

        self.profile("admin", [("Advize-PlantEasily", "2.1.1"), ("sighsorry-AdminQoL", "1.0.0")])
        self.assertEqual(before, valheim_mods.catalog_fingerprint(valheim_mods.player_package_versions(self.store)))

    def test_a_profile_manifest_with_a_utf8_bom_is_still_read(self):
        self.profile("vr", [("Advize-PlantEasily", "2.1.1")])
        self.profile("flat", [("Advize-PlantEasily", "2.1.1")])
        manifest = self.store / "vr/profile-manifest.json"
        manifest.write_bytes(b"\xef\xbb\xbf" + manifest.read_bytes())

        self.assertEqual(list(valheim_mods.player_package_versions(self.store)), ["Advize-PlantEasily"])
