"""Provisioning tests that execute the code, not just import it.

`prepare_profile` called `profile_store` without the module ever being imported, from
09e88b3 on 2026-08-17 until 2026-08-25. Every server creation died with
`NameError: name 'profile_store' is not defined`, and the pytest gate stayed green for
eight days because nothing here ever reached line 114. Importing the module would not have
caught it either: in Python an undefined global resolves when the line runs, not when the
file loads. So these tests call the functions.
"""

import struct

import pytest

import profile_store
import valheim_provision as provision
import valheim_world


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A world root the provisioning helpers resolve against, with no real fleet in it."""
    root = tmp_path / "fleet"
    root.mkdir()
    monkeypatch.setenv("VALHEIM_WORLD_ROOT", str(root))
    return root


def test_prepare_profile_creates_a_profile_and_links_the_staged_world(fleet, tmp_path):
    """Executes the line that carried the NameError for eight days.

    The assertion is deliberately on the effect - a manifest on disk under the shared
    profile store - because that is what proves prepare_profile ran to completion rather
    than raising something the caller swallowed.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    destination = provision.prepare_profile(stage, "default", "")
    assert profile_store.manifest_path("default", profile_store.profiles_root(fleet)).is_file()
    assert destination == profile_store.profile_dir("default", profile_store.profiles_root(fleet))
    assert (stage / "mods" / ".active-mod-profile").read_text() == "default\n"


def test_prepare_profile_refuses_to_copy_into_a_profile_that_exists(fleet, tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    provision.prepare_profile(stage, "shared", "")
    with pytest.raises(RuntimeError, match="profile already exists"):
        provision.prepare_profile(stage, "shared", "other")


def build_metadata(tmp_path, name, seed="qmrbecQI2K"):
    """A .fwl in the shape of the live ones: 46-byte package, world version 37."""
    path = tmp_path / (name + ".fwl")
    valheim_world.save(path, {
        "name": name, "seed": seed, "seed_value": -1926674633, "uid": -2018127128,
        "world_version": 37, "generator_version": 2, "trailer": b"\x01" + struct.pack("<i", 0),
    })
    return path


def test_place_save_pair_rewrites_only_the_world_name(tmp_path):
    """Renaming the two files is not enough: the .fwl carries the world's own name.

    Everything else is what makes the placed world the SAME world rather than a fresh one
    on the same map, so seed, seed value, UID, world version, generator version and the
    trailer all have to survive the rewrite untouched.
    """
    source_db = tmp_path / "Hrafnheim.db"
    source_db.write_bytes(b"world database" * 64)
    source_fwl = build_metadata(tmp_path, "Hrafnheim")
    before = valheim_world.parse(source_fwl)

    destination = tmp_path / "worlds_local"
    destination.mkdir()
    provision.place_save_pair(source_db, source_fwl, destination, "Nyheim")

    assert (destination / "Nyheim.db").read_bytes() == source_db.read_bytes()
    after = valheim_world.parse(destination / "Nyheim.fwl")
    assert after["name"] == "Nyheim"
    assert {key: value for key, value in after.items() if key != "name"} == \
           {key: value for key, value in before.items() if key != "name"}

    # Round trip: rewriting the name back reproduces the original bytes exactly. If the
    # rewrite had dropped or reordered any field this cannot hold.
    after["name"] = "Hrafnheim"
    body = valheim_world.body(after)
    assert struct.pack("<i", len(body)) + body == source_fwl.read_bytes()


def test_place_save_pair_names_which_file_is_wrong(tmp_path):
    """A missing file and a symlink need different reactions, so they get different words."""
    destination = tmp_path / "worlds_local"
    destination.mkdir()
    source_db = tmp_path / "Hrafnheim.db"
    source_db.write_bytes(b"world database")
    source_fwl = build_metadata(tmp_path, "Hrafnheim")

    with pytest.raises(RuntimeError, match="world save world metadata is missing"):
        provision.place_save_pair(source_db, tmp_path / "absent.fwl", destination, "Nyheim")
    with pytest.raises(RuntimeError, match="world save database is missing"):
        provision.place_save_pair(tmp_path / "absent.db", source_fwl, destination, "Nyheim")

    linked = tmp_path / "linked.db"
    linked.symlink_to(source_db)
    with pytest.raises(RuntimeError, match="world save database is a symbolic link"):
        provision.place_save_pair(linked, source_fwl, destination, "Nyheim")

    linked_fwl = tmp_path / "linked.fwl"
    linked_fwl.symlink_to(source_fwl)
    with pytest.raises(RuntimeError, match="world save world metadata is a symbolic link"):
        provision.place_save_pair(source_db, linked_fwl, destination, "Nyheim")


def test_prepare_world_places_an_uploaded_pair_under_the_new_world_name(fleet, tmp_path):
    """The upload branch of prepare_world, on the fixed names the portal stages."""
    upload = tmp_path / "spool" / "9f2c1ab34de5677890abcdef01234567"
    upload.mkdir(parents=True)
    (upload / "world.db").write_bytes(b"uploaded database" * 32)
    build_metadata(upload, "world").rename(upload / "world.fwl")
    # The staged metadata still names the world the operator exported, not the new server.
    valheim_world.save(upload / "world.fwl", {
        "name": "Hrafnheim", "seed": "qmrbecQI2K", "seed_value": -1926674633, "uid": -2018127128,
        "world_version": 37, "generator_version": 2, "trailer": b"\x01" + struct.pack("<i", 0),
    })

    stage = tmp_path / "stage"
    stage.mkdir()
    args = provision.argparse.Namespace(world="Nyheim", source_world="", world_upload=str(upload))
    provision.prepare_world(stage, args)

    placed = stage / "config_merged" / "worlds_local"
    assert sorted(path.name for path in placed.iterdir()) == ["Nyheim.db", "Nyheim.fwl"]
    metadata = valheim_world.parse(placed / "Nyheim.fwl")
    assert metadata["name"] == "Nyheim"
    assert metadata["seed"] == "qmrbecQI2K" and metadata["uid"] == -2018127128


def test_prepare_world_refuses_a_staged_upload_that_is_not_there(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    args = provision.argparse.Namespace(
        world="Nyheim", source_world="", world_upload=str(tmp_path / "absent"))
    with pytest.raises(RuntimeError, match="staged world upload is unavailable"):
        provision.prepare_world(stage, args)


def test_prepare_world_leaves_worlds_local_empty_for_a_generated_world(tmp_path):
    """Control for the two placement tests: seed and random modes place nothing.

    Valheim treats a .fwl whose .db is missing as no world at all and regenerates it, so
    the empty directory is the correct outcome rather than a missing step.
    """
    stage = tmp_path / "stage"
    stage.mkdir()
    args = provision.argparse.Namespace(world="Nyheim", source_world="", world_upload="")
    provision.prepare_world(stage, args)
    assert list((stage / "config_merged" / "worlds_local").iterdir()) == []
