"""Provisioning tests that execute the code, not just import it.

`prepare_profile` called `profile_store` without the module ever being imported, from
09e88b3 on 2026-08-17 until 2026-08-25. Every server creation died with
`NameError: name 'profile_store' is not defined`, and the pytest gate stayed green for
eight days because nothing here ever reached line 114. Importing the module would not have
caught it either: in Python an undefined global resolves when the line runs, not when the
file loads. So these tests call the functions.
"""

import pytest

import profile_store
import valheim_provision as provision


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
