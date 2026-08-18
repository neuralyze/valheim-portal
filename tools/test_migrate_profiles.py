"""The one-time fold: four per-world copies into one shared profile.

The tests that matter are the refusals. A migration that moves 2.1 GB out from
under a running container, or that silently picks one world's mod set as the
winner, is worse than no migration at all - the fleet has already been taken down
twice this month by a mod change nobody could see the shape of.

`test_a_fold_sets_aside_the_copies_it_did_not_take` is the safety property: the
copies that lose are moved, never deleted, because they are the only record of
what those worlds were running.
"""

import json

import pytest

import migrate_profiles as migrate
import profile_store as store


@pytest.fixture(autouse=True)
def stopped_servers(monkeypatch):
    """No containers, unless a test says otherwise."""
    monkeypatch.setattr(migrate, "running_servers", lambda worlds: [])


@pytest.fixture(autouse=True)
def history_store(tmp_path, monkeypatch):
    monkeypatch.setenv("VALHEIM_SETTINGS_HISTORY", str(tmp_path / "settings-history"))


def build_world(fleet, world, profile, packages, *, disabled=(), excluded=(), link=True):
    directory = fleet / world / "mods" / "profiles" / profile
    (directory / "manager-cache" / "server" / "BepInEx" / "plugins").mkdir(parents=True)
    (directory / "client-config").mkdir()
    (directory / "client-config" / "Azumatt.WardIsLove.cfg").write_text(f"WardHotKey = F4 # {world}\n")
    (directory / profile_manifest(directory)).write_text(json.dumps({
        "schema_version": 1,
        "profile_name": f"{world} Redesign",
        "world_name": world,
        "packages": [{"identifier": p, "version": "1.0.0"} for p in packages],
        "client_only_packages": [],
        "disabled_packages": [{"identifier": p, "version": "1.0.0"} for p in disabled],
        "excluded_packages": [{"identifier": p, "version": "1.0.0", "reason": "old"} for p in excluded],
    }))
    if link:
        (fleet / world / "mods" / store.LINK_FILE).write_text(profile + "\n")
    return directory


def profile_manifest(_directory):
    return store.MANIFEST_NAME


@pytest.fixture
def fleet(tmp_path):
    build_world(tmp_path, "Hrafnheim", "redesign-alpha", ["Jotunn", "Smoothbrain-Mining"],
                excluded=["ValheimVR-ValheimVR", "MSchmoecker-VNEI"])
    build_world(tmp_path, "Storgard", "redesign-alpha", ["Jotunn", "Smoothbrain-Mining", "VNEI"],
                disabled=["cybrp-ItemDrawers"], excluded=["ValheimVR-ValheimVR"])
    return tmp_path


def test_plan_names_every_package_that_is_not_in_all_copies(fleet):
    state = migrate.plan(fleet)

    assert [copy["world"] for copy in state["copies"]] == ["Hrafnheim", "Storgard"]
    assert state["identical"] is False
    vnei = next(d for d in state["differences"] if d["package"] == "VNEI")
    assert vnei["in"] == ["Storgard"] and vnei["missing_from"] == ["Hrafnheim"]
    assert any(d.get("disabled_in") == ["Storgard"] for d in state["differences"])


def test_identical_copies_report_that_a_fold_loses_nothing(tmp_path):
    build_world(tmp_path, "Doggerland", "redesign-alpha", ["Jotunn"])
    build_world(tmp_path, "Vangard", "redesign-alpha", ["Jotunn"])

    assert migrate.plan(tmp_path)["identical"] is True


def test_a_running_server_stops_the_migration(fleet, monkeypatch):
    monkeypatch.setattr(migrate, "running_servers", lambda worlds: ["Hrafnheim"])

    with pytest.raises(migrate.MigrationError, match="stop these servers first: Hrafnheim"):
        migrate.apply(fleet, fold="survival", take="Hrafnheim", profiles_root=fleet / "profiles")
    # Nothing moved.
    assert (fleet / "Hrafnheim/mods/profiles/redesign-alpha").is_dir()
    assert not (fleet / "profiles").exists()


def test_a_fold_links_every_world_to_one_profile(fleet):
    root = fleet / "profiles"

    actions = migrate.apply(fleet, fold="survival", take="Hrafnheim", profiles_root=root)

    assert (root / "survival" / store.MANIFEST_NAME).is_file()
    assert store.linked_servers("survival", fleet) == ["Hrafnheim", "Storgard"]
    manifest = json.loads((root / "survival" / store.MANIFEST_NAME).read_text())
    assert "world_name" not in manifest and manifest["profile_name"] == "survival"
    assert manifest["schema_version"] == 2
    # The kept copy carried its configs across rather than being rebuilt.
    assert (root / "survival" / "client-config" / "Azumatt.WardIsLove.cfg").read_text().endswith("Hrafnheim\n")
    assert any("linked to survival" in action for action in actions)


def test_a_fold_sets_aside_the_copies_it_did_not_take(fleet):
    migrate.apply(fleet, fold="survival", take="Hrafnheim", profiles_root=fleet / "profiles")

    aside = fleet / "Storgard" / "mods" / migrate.SET_ASIDE / "redesign-alpha"
    assert (aside / store.MANIFEST_NAME).is_file()
    kept = json.loads((aside / store.MANIFEST_NAME).read_text())
    assert [p["identifier"] for p in kept["packages"]] == ["Jotunn", "Smoothbrain-Mining", "VNEI"]
    assert not (fleet / "Storgard" / "mods" / "profiles" / "redesign-alpha").exists()


def test_separate_gives_each_world_its_own_profile(fleet):
    root = fleet / "profiles"

    migrate.apply(fleet, separate=True, profiles_root=root)

    assert store.profile_names(root) == ["Hrafnheim-redesign-alpha", "Storgard-redesign-alpha"]
    assert store.linked_profile(fleet / "Storgard") == "Storgard-redesign-alpha"
    assert store.linked_servers("Hrafnheim-redesign-alpha", fleet) == ["Hrafnheim"]


def test_fold_and_separate_are_mutually_exclusive(fleet):
    with pytest.raises(migrate.MigrationError, match="exactly one"):
        migrate.apply(fleet, fold="survival", take="Hrafnheim", separate=True,
                      profiles_root=fleet / "profiles")


def test_take_must_name_a_world_that_has_a_copy(fleet):
    with pytest.raises(migrate.MigrationError, match="no copy: 'Nowhere'"):
        migrate.apply(fleet, fold="survival", take="Nowhere", profiles_root=fleet / "profiles")


def test_a_world_with_no_profile_stops_the_plan(tmp_path):
    build_world(tmp_path, "Hrafnheim", "redesign-alpha", ["Jotunn"])
    (tmp_path / "Empty" / "mods").mkdir(parents=True)

    with pytest.raises(migrate.MigrationError, match="Empty has no profile"):
        migrate.plan(tmp_path)


def test_a_link_pointing_at_a_missing_profile_stops_the_plan(fleet):
    (fleet / "Hrafnheim" / "mods" / store.LINK_FILE).write_text("gone\n")

    with pytest.raises(migrate.MigrationError, match="links to gone"):
        migrate.plan(fleet)


def test_several_profiles_and_no_link_is_a_refusal_not_a_guess(tmp_path):
    build_world(tmp_path, "Hrafnheim", "redesign-alpha", ["Jotunn"], link=False)
    build_world(tmp_path, "Hrafnheim", "experiment", ["Jotunn", "VNEI"], link=False)

    with pytest.raises(migrate.MigrationError, match="2 profiles and no link file"):
        migrate.plan(tmp_path)


def test_the_settings_are_recorded_before_anything_moves(fleet, tmp_path):
    migrate.apply(fleet, fold="survival", take="Hrafnheim", profiles_root=fleet / "profiles")

    store_path = tmp_path / "settings-history"
    recorded = store_path / "Storgard/profiles/redesign-alpha/client-config/Azumatt.WardIsLove.cfg"
    assert recorded.is_file()  # the copy that lost is in history, not only on disk
    assert "Storgard" in recorded.read_text()


def test_a_setting_shared_by_every_world_is_not_a_difference(fleet):
    """The bug this catches: 28 reported differences for a fleet that had three.

    Every world excludes ValheimVR, so that exclusion is the shared mod set. Only
    Hrafnheim excludes VNEI, and that one is a decision the fold has to make.
    """
    reported = {d["package"] for d in migrate.plan(fleet)["differences"]}

    assert "ValheimVR-ValheimVR" not in reported
    assert {"VNEI", "MSchmoecker-VNEI", "cybrp-ItemDrawers"} == reported
