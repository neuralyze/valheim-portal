"""The shared profile model: one definition, many servers, no per-server mod list.

Each test here is one rule from the design rather than a unit of code:

* editing a profile is one edit for every server linked to it (`test_one_edit_reaches_every_linked_server`)
* a copy is independent, which is the entire difference from a link (`test_a_copy_keeps_nothing_pointing_at_its_source`)
* a profile belongs to no world, so `world_name` must not come back (`test_a_shared_profile_names_no_world`)
* a server is linked, never populated by copying another server

The delete guard is tested because the existing tooling already refuses to remove
the profile a world is running; under sharing that guard has to count every linked
server, and getting it wrong empties the mod set of servers nobody was looking at.
"""

import json

import pytest

import profile_store as store


@pytest.fixture
def fleet(tmp_path):
    """A fleet root with two provisioned worlds and an empty profile store."""
    for world in ("Hrafnheim", "Storgard"):
        (tmp_path / world / "mods").mkdir(parents=True)
    (tmp_path / "world_backups").mkdir()  # not a world; must never be listed as one
    return tmp_path


@pytest.fixture
def root(fleet):
    return fleet / "profiles"


def test_worlds_are_the_directories_that_hold_mods(fleet):
    assert [world.name for world in store.worlds_in(fleet)] == ["Hrafnheim", "Storgard"]


def test_one_edit_reaches_every_linked_server(fleet, root):
    store.create("survival", root)
    store.link(fleet / "Hrafnheim", "survival", root)
    store.link(fleet / "Storgard", "survival", root)

    # The edit an operator makes: one file, in one place.
    manifest = store.manifest_path("survival", root)
    data = json.loads(manifest.read_text())
    data["packages"].append({"identifier": "makail-ItemDrawers", "version": "0.5.8"})
    manifest.write_text(json.dumps(data))

    assert store.linked_servers("survival", fleet) == ["Hrafnheim", "Storgard"]
    for world in ("Hrafnheim", "Storgard"):
        name = store.linked_profile(fleet / world)
        packages = json.loads(store.manifest_path(name, root).read_text())["packages"]
        assert [p["identifier"] for p in packages] == ["makail-ItemDrawers"]


def test_a_shared_profile_names_no_world(root):
    store.create("survival", root)
    assert "world_name" not in json.loads(store.manifest_path("survival", root).read_text())


def test_a_copy_keeps_nothing_pointing_at_its_source(fleet, root):
    store.create("survival", root)
    manifest = store.manifest_path("survival", root)
    data = json.loads(manifest.read_text())
    data["packages"].append({"identifier": "Smoothbrain-Mining", "version": "1.1.6"})
    manifest.write_text(json.dumps(data))

    store.copy("survival", "hardcore", root)
    copied = json.loads(store.manifest_path("hardcore", root).read_text())
    assert copied["profile_name"] == "hardcore"
    assert [p["identifier"] for p in copied["packages"]] == ["Smoothbrain-Mining"]
    assert "copied_from" not in copied and "world_name" not in copied

    # Independence in the direction that matters: the source moves on, the copy does not.
    data["packages"].append({"identifier": "Azumatt-AzuAutoStore", "version": "3.0.14"})
    manifest.write_text(json.dumps(data))
    still = json.loads(store.manifest_path("hardcore", root).read_text())["packages"]
    assert [p["identifier"] for p in still] == ["Smoothbrain-Mining"]


def test_a_copy_of_a_missing_profile_is_a_named_refusal(root):
    with pytest.raises(store.ProfileError, match="no such profile: ghost"):
        store.copy("ghost", "new", root)


def test_creating_over_an_existing_profile_is_refused(root):
    store.create("survival", root)
    with pytest.raises(store.ProfileError, match="already exists"):
        store.create("survival", root)


def test_a_server_cannot_link_to_a_profile_that_does_not_exist(fleet, root):
    # A link to nothing would deploy an empty mod set, which players read as
    # "every mod is gone" rather than as a mistyped name.
    with pytest.raises(store.ProfileError, match="no such profile: typo"):
        store.link(fleet / "Hrafnheim", "typo", root)
    assert store.linked_profile(fleet / "Hrafnheim") is None


def test_relinking_replaces_the_link_rather_than_accumulating(fleet, root):
    store.create("survival", root)
    store.create("creative", root)
    store.link(fleet / "Hrafnheim", "survival", root)
    store.link(fleet / "Hrafnheim", "creative", root)

    assert store.linked_profile(fleet / "Hrafnheim") == "creative"
    assert store.linked_servers("survival", fleet) == []
    assert store.linked_servers("creative", fleet) == ["Hrafnheim"]


def test_a_linked_profile_cannot_be_deleted(fleet, root):
    store.create("survival", root)
    store.link(fleet / "Storgard", "survival", root)

    with pytest.raises(store.ProfileError, match="linked by Storgard"):
        store.delete("survival", fleet, root)
    assert store.profile_dir("survival", root).is_dir()

    store.create("creative", root)
    store.link(fleet / "Storgard", "creative", root)
    store.delete("survival", fleet, root)
    assert not store.profile_dir("survival", root).exists()


def test_names_that_could_escape_the_store_are_refused(root):
    for name in ("../escape", "/absolute", "", ".hidden", "with space"):
        with pytest.raises(store.ProfileError, match="invalid profile name"):
            store.profile_dir(name, root)


def test_describe_reports_what_each_profile_drives(fleet, root):
    store.create("survival", root)
    store.create("unused", root)
    store.link(fleet / "Hrafnheim", "survival", root)
    store.link(fleet / "Storgard", "survival", root)

    rows = {row["profile"]: row for row in store.describe(fleet, root)}
    assert rows["survival"]["servers"] == ["Hrafnheim", "Storgard"]
    assert rows["unused"]["servers"] == []


def test_the_store_defaults_beside_the_worlds(fleet, monkeypatch):
    monkeypatch.delenv(store.PROFILE_ROOT_ENVIRONMENT, raising=False)
    assert store.profiles_root(fleet) == fleet / "profiles"

    monkeypatch.setenv(store.PROFILE_ROOT_ENVIRONMENT, "relative")
    with pytest.raises(store.ProfileError, match="absolute"):
        store.profiles_root(fleet)


def test_the_cli_links_and_reports(fleet, root, capsys):
    arguments = ["--profiles-root", str(root), "--fleet-root", str(fleet)]
    assert store.main([*arguments, "create", "survival"]) == 0
    assert store.main([*arguments, "link", "Hrafnheim", "survival"]) == 0
    capsys.readouterr()

    assert store.main([*arguments, "linked", "Hrafnheim"]) == 0
    assert capsys.readouterr().out.strip() == "survival"

    assert store.main([*arguments, "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{
        "profile": "survival", "name": "survival", "packages": 0,
        "disabled_packages": 0, "custom_packages": 0, "servers": ["Hrafnheim"],
    }]
