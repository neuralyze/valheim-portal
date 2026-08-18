"""The settings store: it exists so a removal cannot be the end of a config.

The test that matters is `test_a_removed_config_is_still_readable`: every other
property here is in service of that one, because the store's only real promise is
that `show` keeps working *after* the file is gone. A store that answers "no
history" at exactly the moment an operator needs it would pass a naive
round-trip test and fail the job.

The 2.1 GB package cache and the shipped plugin trees are asserted absent rather
than assumed absent: mirroring those was the reason a profile could not simply be
git-initialised in place.
"""

import subprocess

import pytest

import settings_history as history


def build_world(root, *, world="Hrafnheim", profile="redesign-alpha"):
    """A world with the four places settings actually live, plus the traps."""
    world_dir = root / world
    profile_dir = world_dir / "mods" / "profiles" / profile
    (profile_dir / "client-config").mkdir(parents=True)
    (profile_dir / "client-config-vr").mkdir()
    (profile_dir / "manager-cache" / "client" / "BepInEx" / "plugins").mkdir(parents=True)
    server_config = world_dir / "config_merged" / "bepinex"
    (server_config / "plugins" / "Cybrp-ItemDrawers").mkdir(parents=True)

    (profile_dir / "profile-manifest.json").write_text('{"profile_name": "Redesign Test"}\n')
    (profile_dir / "client-config" / "Azumatt.WardIsLove.cfg").write_text("WardHotKey = F4\n")
    (profile_dir / "client-config-vr" / "neuralyze.vrfixes.cfg").write_text("ward = key:F4\n")
    (server_config / "cybrp.ItemDrawers.cfg").write_text("Plugin GUID: cybrp-ItemDrawers\nrows = 4\n")

    # Traps: payload that must never enter the store.
    (profile_dir / "manager-cache" / "client" / "BepInEx" / "plugins" / "Jotunn.dll").write_bytes(b"MZ")
    (server_config / "plugins" / "Cybrp-ItemDrawers" / "drawers.cfg").write_text("shipped\n")
    (profile_dir / "client-config" / "Azumatt.WardIsLove.cfg.before-20260817").write_text("WardHotKey = G\n")
    return world_dir


def test_the_store_holds_settings_and_nothing_else(tmp_path):
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"

    assert history.snapshot(world, "first", store)

    recorded = sorted(
        path.relative_to(store).as_posix()
        for path in store.rglob("*")
        if path.is_file() and ".git" not in path.parts and path.name != "README.md"
    )
    assert recorded == [
        "Hrafnheim/profiles/redesign-alpha/client-config-vr/neuralyze.vrfixes.cfg",
        "Hrafnheim/profiles/redesign-alpha/client-config/Azumatt.WardIsLove.cfg",
        "Hrafnheim/profiles/redesign-alpha/profile-manifest.json",
        "Hrafnheim/server-config/cybrp.ItemDrawers.cfg",
    ]


def test_an_unchanged_world_is_not_a_commit(tmp_path):
    # A `list` or a dry-run `update` runs the hook too. Recording an empty commit
    # per read would bury the operations that changed something.
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"

    assert history.snapshot(world, "first", store)
    assert history.snapshot(world, "second", store) is None


def test_a_removed_config_is_still_readable(tmp_path):
    """The promise: the mod is gone, its settings are not."""
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"
    history.snapshot(world, "before removing ItemDrawers", store)

    deleted = world / "config_merged" / "bepinex" / "cybrp.ItemDrawers.cfg"
    deleted.unlink()
    assert history.snapshot(world, "remove cybrp-ItemDrawers", store)

    relative = "Hrafnheim/server-config/cybrp.ItemDrawers.cfg"
    assert not (store / relative).exists()  # the deletion is recorded as a deletion
    reference, content = history.last_version(store, relative)
    assert "rows = 4" in content
    assert reference.endswith("~1")  # read from before the commit that deleted it


def test_an_edit_keeps_the_previous_value_reachable(tmp_path):
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"
    history.snapshot(world, "ward on F4", store)

    ward = world / "profiles-not-used"  # guard against a typo passing silently
    assert not ward.exists()
    live = world / "mods" / "profiles" / "redesign-alpha" / "client-config" / "Azumatt.WardIsLove.cfg"
    live.write_text("WardHotKey = F7\n")
    history.snapshot(world, "move ward to F7", store)

    relative = "Hrafnheim/profiles/redesign-alpha/client-config/Azumatt.WardIsLove.cfg"
    reference, content = history.last_version(store, relative)
    assert reference == "HEAD" and "F7" in content
    earlier = subprocess.run(["git", "-C", str(store), "show", "HEAD~1:" + relative],
                             capture_output=True, text=True, check=True).stdout
    assert "F4" in earlier


def test_history_lists_the_commits_that_touched_one_file(tmp_path):
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"
    history.snapshot(world, "first", store)
    (world / "config_merged" / "bepinex" / "cybrp.ItemDrawers.cfg").write_text(
        "Plugin GUID: cybrp-ItemDrawers\nrows = 6\n")
    history.snapshot(world, "widen the drawers", store)

    lines = history.history(store, "Hrafnheim/server-config/cybrp.ItemDrawers.cfg")
    assert len(lines) == 2 and "widen the drawers" in lines[0]


def test_a_file_never_recorded_is_a_named_failure(tmp_path):
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"
    history.snapshot(world, "first", store)

    with pytest.raises(history.HistoryError, match="no history"):
        history.last_version(store, "Hrafnheim/server-config/never-existed.cfg")


def test_the_store_defaults_beside_the_worlds(tmp_path, monkeypatch):
    # The fleet root, not inside a world: the same settings are about to be
    # shared between servers, and a store inside Hrafnheim would then be a lie.
    monkeypatch.delenv(history.STORE_ENVIRONMENT, raising=False)
    world = build_world(tmp_path)
    assert history.store_path(world) == tmp_path / "settings-history"

    monkeypatch.setenv(history.STORE_ENVIRONMENT, str(tmp_path / "elsewhere"))
    assert history.store_path(world) == tmp_path / "elsewhere"

    monkeypatch.setenv(history.STORE_ENVIRONMENT, "relative/path")
    with pytest.raises(history.HistoryError, match="absolute"):
        history.store_path(world)


def test_the_cli_restores_outside_the_live_tree(tmp_path, capsys):
    world = build_world(tmp_path)
    store = tmp_path / "settings-history"
    history.snapshot(world, "first", store)
    live = world / "config_merged" / "bepinex" / "cybrp.ItemDrawers.cfg"
    live.unlink()
    history.snapshot(world, "remove cybrp-ItemDrawers", store)

    destination = tmp_path / "recovered.cfg"
    assert history.main(["--store", str(store), "restore",
                         "Hrafnheim/server-config/cybrp.ItemDrawers.cfg",
                         "--to", str(destination)]) == 0
    assert "rows = 4" in destination.read_text()
    assert not live.exists()  # recovery reads history; it does not deploy
    assert "restored=" in capsys.readouterr().out
