"""Per-key merge of a server's overrides over a shared profile's settings.

The property that matters is `test_a_later_profile_change_reaches_a_server_that_overrode_one_key`:
whole-file overrides pass a naive round-trip test and still cause the bug this module
exists to prevent - the override file keeps yesterday's value for every OTHER setting,
so a profile edit silently fails to reach the server that overrode one line.

The BepInEx dialect is taken from real files on the fleet: `##` describes the file, `#`
describes the next key, section headers can contain colour markup like
`[<color#00FFFF>Thor</color>]`, and values may be empty.
"""

import config_merge as merge

WARD = """\
## Settings file was created by plugin WardIsLove v3.7.2
## Plugin GUID: Azumatt.WardIsLove

[<color#00FFFF>Thor</color>]

## Build Category where Thor is available.
# Setting type: BuildPieceCategory
# Default value: Misc
Build Table Category = Misc

# Setting type: String
# Default value: 
Custom Build Category = 

[General]

# Setting type: KeyboardShortcut
WardHotKey = F4
"""


def test_only_the_overridden_key_changes(tmp_path):
    merged = merge.merge(WARD, "[General]\nWardHotKey = F7\n")

    assert "WardHotKey = F7" in merged
    assert "Build Table Category = Misc" in merged
    # The profile's text survives: comments, blank lines and section order.
    assert merged.count("## Settings file was created by plugin") == 1
    assert "## Build Category where Thor is available." in merged
    assert merged.index("[<color#00FFFF>Thor</color>]") < merged.index("[General]")


def test_a_later_profile_change_reaches_a_server_that_overrode_one_key():
    """The whole point: the override carries one key, not a snapshot of the file."""
    override = "[General]\nWardHotKey = F7\n"
    # The profile moves on: a default changes and a setting is added.
    moved_on = WARD.replace("Build Table Category = Misc", "Build Table Category = Furniture") \
                   .replace("[General]", "[General]\n\n# Setting type: Boolean\nShowMarker = false")

    merged = merge.merge(moved_on, override)

    assert "Build Table Category = Furniture" in merged  # profile change landed
    assert "ShowMarker = false" in merged                # new setting landed
    assert "WardHotKey = F7" in merged                   # override still applied


def test_the_same_key_in_two_sections_stays_in_its_own_section():
    base = "[A]\nKey = 1\n\n[B]\nKey = 2\n"

    merged = merge.merge(base, "[B]\nKey = 9\n")

    assert merged.index("Key = 1") < merged.index("[B]")
    assert "Key = 9" in merged and "Key = 2" not in merged


def test_a_key_the_profile_never_wrote_is_added_under_its_section():
    merged = merge.merge(WARD, "[General]\nShowMarker = true\n")

    lines = merged.splitlines()
    assert "ShowMarker = true" in lines
    # Under [General], not appended at the end of the file in the wrong section.
    assert lines.index("ShowMarker = true") > lines.index("[General]")
    assert "## server override" in merged


def test_a_section_the_profile_never_wrote_is_appended():
    merged = merge.merge(WARD, "[Extra]\nEnabled = true\n")

    assert "[Extra]" in merged and "Enabled = true" in merged
    assert merged.index("[General]") < merged.index("[Extra]")


def test_an_empty_override_returns_the_profile_unchanged():
    assert merge.merge(WARD, "") == WARD
    assert merge.merge(WARD, "## only a comment\n") == WARD


def test_an_empty_value_is_a_value():
    merged = merge.merge(WARD, "[<color#00FFFF>Thor</color>]\nCustom Build Category = Runes\n")
    assert "Custom Build Category = Runes" in merged

    cleared = merge.merge(merged, "[<color#00FFFF>Thor</color>]\nCustom Build Category =\n")
    assert "Custom Build Category = " in cleared or "Custom Build Category =" in cleared


def test_merge_tree_reports_which_files_a_server_changed(tmp_path):
    base, override, out = tmp_path / "profile", tmp_path / "override", tmp_path / "merged"
    base.mkdir()
    override.mkdir()
    (base / "Azumatt.WardIsLove.cfg").write_text(WARD)
    (base / "untouched.cfg").write_text("[A]\nKey = 1\n")
    (base / "shipped.yml").write_text("servers: []\n")
    (override / "Azumatt.WardIsLove.cfg").write_text("[General]\nWardHotKey = F7\n")

    touched = merge.merge_tree(base, override, out)

    assert touched == ["Azumatt.WardIsLove.cfg"]
    assert "WardHotKey = F7" in (out / "Azumatt.WardIsLove.cfg").read_text()
    assert (out / "untouched.cfg").read_text() == "[A]\nKey = 1\n"
    assert (out / "shipped.yml").read_text() == "servers: []\n"


def test_an_override_for_a_file_the_profile_lacks_is_carried_and_named(tmp_path):
    # Dropping it would mean a setting an operator wrote never reaches the server, with
    # nothing said about it.
    base, override, out = tmp_path / "profile", tmp_path / "override", tmp_path / "merged"
    base.mkdir()
    override.mkdir()
    (base / "known.cfg").write_text("[A]\nKey = 1\n")
    (override / "only-here.cfg").write_text("[B]\nKey = 2\n")

    touched = merge.merge_tree(base, override, out)

    assert (out / "only-here.cfg").is_file()
    assert any("only-here.cfg (override only)" == entry for entry in touched)


def test_a_non_cfg_override_does_not_get_key_merged(tmp_path):
    # Only the INI dialect is understood. A YAML or JSON override is copied whole rather
    # than half-parsed into something neither format.
    base, override, out = tmp_path / "profile", tmp_path / "override", tmp_path / "merged"
    base.mkdir()
    override.mkdir()
    (base / "Azumatt.FastLink_servers.yml").write_text("servers:\n  - name: base\n")
    (override / "Azumatt.FastLink_servers.yml").write_text("servers:\n  - name: mine\n")

    touched = merge.merge_tree(base, override, out)

    assert (out / "Azumatt.FastLink_servers.yml").read_text() == "servers:\n  - name: mine\n"
    # Reported as coarse, so nobody assumes a per-key merge happened.
    assert touched == ["Azumatt.FastLink_servers.yml (whole file)"]
