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


# The 53 bytes of Ulfsland's _main.0.fwl2, read off the live 1.0.12 world on 2026-09-12:
# int32 declared length 49, int32 world version 41, the 7-bit-prefixed name "Ulfsland",
# the seed name "8JiFcknsJd", int32 seed 1099286780, int64 uid -507977092, int32
# generator version 2, then a nine-byte trailer. A real file rather than a fabricated
# one, because the whole question this answers is whether the 1.0 metadata container is
# the same as the 0.220 one.
ULFSLAND_FWL2 = bytes.fromhex(
    "3100000029000000"
    "08556c66736c616e64"
    "0a384a6946636b6e734a64"
    "fcc88541"
    "7ce2b8e1ffffffff"
    "02000000"
    "000000000000000000"
)


def build_directory_world(parent, name, generations=(0, 1)):
    """A 1.0-format world directory in the shape the game writes.

    Two generations by default: the game keeps the older set beside the current one and
    selects by generation number, so a rename that only reaches the newest .fwl2 leaves
    the world named inconsistently depending on which set the server loads.
    """
    directory = parent / name
    directory.mkdir(parents=True)
    for generation in generations:
        (directory / f"_main.{generation}.fwl2").write_bytes(ULFSLAND_FWL2)
        (directory / f"_main.{generation}.db2").write_bytes(b"zone data" * 512)
        (directory / f"_main.{generation}.chunks").write_bytes(b")\x00S\x00\x00\x00")
        (directory / f"_main.{generation}.ok").write_bytes(struct.pack("<i", 41))
    (directory / "00_00__0_1.chunk").write_bytes(b"zdo" * 64)
    return directory


def test_place_save_directory_rewrites_the_name_in_every_generation(tmp_path):
    """A 1.0 world is copied whole, renamed only where the name actually lives.

    Same contract as place_save_pair: the placed world must be the SAME world on the same
    map, so the seed, seed value, UID, world version, generator version and trailer all
    survive, and the bulk files are copied byte for byte under their original basenames -
    the generation number in _main.<N>.db2 is how the server picks the newest save set.
    """
    source = build_directory_world(tmp_path / "source", "Ulfsland")
    destination = tmp_path / "worlds_local"
    destination.mkdir()

    provision.place_save_directory(source, destination, "Nyheim")

    placed = destination / "Nyheim"
    assert sorted(path.name for path in placed.iterdir()) == [
        "00_00__0_1.chunk",
        "_main.0.chunks", "_main.0.db2", "_main.0.fwl2", "_main.0.ok",
        "_main.1.chunks", "_main.1.db2", "_main.1.fwl2", "_main.1.ok",
    ]
    assert (placed / "_main.1.db2").read_bytes() == (source / "_main.1.db2").read_bytes()
    assert (placed / "_main.1.ok").read_bytes() == struct.pack("<i", 41)
    original = valheim_world.parse(source / "_main.1.fwl2")
    for generation in (0, 1):
        metadata = valheim_world.parse(placed / f"_main.{generation}.fwl2")
        assert metadata["name"] == "Nyheim"
        assert metadata["seed"] == original["seed"] == "8JiFcknsJd"
        assert metadata["seed_value"] == original["seed_value"]
        assert metadata["uid"] == original["uid"]
        assert metadata["world_version"] == 41
        assert metadata["generator_version"] == original["generator_version"]
        assert metadata["trailer"] == original["trailer"]
    # The source is not touched: a copy-a-world-on-this-host provision must not rename
    # the world it copied from.
    assert valheim_world.parse(source / "_main.1.fwl2")["name"] == "Ulfsland"


def test_place_save_directory_refuses_what_is_not_a_world(tmp_path):
    destination = tmp_path / "worlds_local"
    destination.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="holds no .fwl2 metadata"):
        provision.place_save_directory(empty, destination, "Nyheim")
    with pytest.raises(RuntimeError, match="world save directory is missing"):
        provision.place_save_directory(tmp_path / "absent", destination, "Nyheim")

    source = build_directory_world(tmp_path / "linked", "Ulfsland", generations=(1,))
    (source / "_main.1.sneak").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(RuntimeError, match="symbolic link, which is never followed"):
        provision.place_save_directory(source, destination, "Sneakheim")


def test_prepare_world_copies_a_directory_format_source_world(fleet, tmp_path):
    """Copying Ulfsland, which is 1.0-format, onto a new server."""
    build_directory_world(fleet / "Ulfsland" / "config_merged" / "worlds_local", "Ulfsland")
    stage = tmp_path / "stage"
    stage.mkdir()
    args = provision.argparse.Namespace(world="Nyheim", source_world="Ulfsland", world_upload="")
    provision.prepare_world(stage, args)

    placed = stage / "config_merged" / "worlds_local"
    assert [path.name for path in placed.iterdir()] == ["Nyheim"]
    assert valheim_world.parse(placed / "Nyheim" / "_main.1.fwl2")["name"] == "Nyheim"


def test_prepare_world_copies_a_pair_source_world_as_a_pair(fleet, tmp_path):
    """Control: an old-format source world must NOT be taken for a 1.0 one.

    The four worlds that have not upgraded are the rollback path, and worlds_local can
    hold a directory that is not a world, so "a directory exists" is not the test - it
    has to hold a *.fwl2.
    """
    source_root = fleet / "Vangard" / "config_merged" / "worlds_local"
    source_root.mkdir(parents=True)
    (source_root / "Vangard.db").write_bytes(b"world database" * 64)
    build_metadata(source_root, "Vangard").rename(source_root / "Vangard.fwl")
    valheim_world.save(source_root / "Vangard.fwl", {
        "name": "Vangard", "seed": "qmrbecQI2K", "seed_value": -1926674633, "uid": -2018127128,
        "world_version": 37, "generator_version": 2, "trailer": b"\x01" + struct.pack("<i", 0),
    })
    stray = source_root / "Vangard"
    stray.mkdir()
    (stray / "README").write_text("not a world\n")

    stage = tmp_path / "stage"
    stage.mkdir()
    args = provision.argparse.Namespace(world="Nyheim", source_world="Vangard", world_upload="")
    provision.prepare_world(stage, args)

    placed = stage / "config_merged" / "worlds_local"
    assert sorted(path.name for path in placed.iterdir()) == ["Nyheim.db", "Nyheim.fwl"]
    assert valheim_world.parse(placed / "Nyheim.fwl")["world_version"] == 37
