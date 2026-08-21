"""The config schema: it exists so the settings page never guesses a widget or a policy.

Three tests carry the feature and the rest support them.

`test_not_synced_is_not_synced` is the first. The annotation ``[Not Synced with Server]``
contains the string ``Synced with Server``, so a substring test marks 237 explicitly
client-side keys in the live corpus as server-authoritative - the exact inversion of what
their author wrote. That mistake was made twice on 2026-08-21, once in a broadcast to six
agents, and it is invisible in the output: the page renders, the badge is just wrong, and a
policy the admin cannot set is the symptom a player eventually reports.

`test_declaring_nothing_is_a_third_state` is the second. Silence is not a declaration. A
tri-state carried only by an absent json key does not survive decoding into a Go bool, which
is why the emitter uses two flags; if they ever collapsed into one boolean this test fails.

`test_a_section_name_can_contain_markup_and_a_hash` is the third. Deciding a line's kind from
anything but its leading character turns the real header ``[<color#00FFFF>Thor</color>]``
into ``[<color``, and the section plus every key in it silently disappears.

The parsing tests are written from real records in ``<world>/config_merged/bepinex`` rather
than invented shapes, because each encodes a way this host's files differ from the documented
ideal: CRLF endings, a UTF-8 BOM, a ``#`` inside a name, a ``=`` inside a value, one hash for
metadata and two for prose.
"""

import json

import pytest

import valheim_config_schema as schema


# A real record, Azumatt.AzuAntiArthriticCrafting.cfg:1-13, with its file header. The header
# is included on purpose: it is what a naive parser hands to the first entry as a description.
AAA_CRAFTING = """\
## Settings file was created by plugin AzuAntiArthriticCrafting v2.1.6
## Plugin GUID: Azumatt.AzuAntiArthriticCrafting

[1 - Crafting Tweaks]

## If enabled, the crafting amount will be reset when the recipe is changed [Synced with Server]
# Setting type: Toggle
# Default value: On
# Acceptable values: Off, On
Reset Crafting Value = On

## Personal hotkey, yours alone [Not Synced with Server]
# Setting type: KeyboardShortcut
# Default value: G
Hot Key = LeftShift + PageUp
"""


def entries(sections):
    return {entry["key"]: entry for section in sections for entry in section["entries"]}


def parsed(text):
    return entries(schema.parse_config(text)[0])


def test_not_synced_is_not_synced():
    """[Not Synced with Server] must never set `synced`, because it contains that string."""
    found = parsed(AAA_CRAFTING)
    assert found["Reset Crafting Value"]["synced"] is True
    assert "client_side" not in found["Reset Crafting Value"]
    # The whole point. A substring test puts synced=True here and lies about the key.
    assert "synced" not in found["Hot Key"]
    assert found["Hot Key"]["client_side"] is True


def test_declaring_nothing_is_a_third_state():
    """No annotation is neither declaration, and must stay distinguishable from both."""
    found = parsed("[General]\n"
                   "## A setting whose author said nothing about ServerSync\n"
                   "# Setting type: Boolean\n# Default value: false\nQuiet = false\n")["Quiet"]
    assert "synced" not in found
    assert "client_side" not in found


@pytest.mark.parametrize("marker,field", [
    ("[Synced with Server]", "synced"),
    ("[Synced with server]", "synced"),
    ("[Not Synced with Server]", "client_side"),
    ("[Not synchronized with server]", "client_side"),
])
def test_every_annotation_spelling_on_this_host(marker, field):
    """All four spellings present in the live corpus, and the annotation leaves the prose."""
    found = parsed(f"[S]\n## Prose. {marker}\n# Setting type: Boolean\nK = true\n")["K"]
    assert found[field] is True
    assert found["description"] == "Prose."
    assert marker not in found["description"]


def test_a_section_name_can_contain_markup_and_a_hash():
    """`[<color#00FFFF>Thor</color>]` is a real header, Azumatt.WardIsLove.cfg:4.

    A pass that strips from a mid-line `#` before deciding the line's kind makes it
    `[<color`, and the section and its keys vanish. The kind comes from the leading
    character alone.
    """
    sections, _, _ = schema.parse_config(
        "[<color#00FFFF>Thor</color>]\n# Setting type: String\nTools = Hammer\n"
        "[General]\n# Setting type: Boolean\nPlain = true\n")
    assert [section["name"] for section in sections] == ["<color#00FFFF>Thor</color>", "General"]
    assert sections[0]["entries"][0]["key"] == "Tools"


def test_a_hash_inside_a_key_name_is_not_a_comment():
    """`<color#00FFFF>Thor</color> Comfort` is a real key, xyz.alcan.comfortcalc.cfg:739.

    Its plain neighbour `Armour Stand Comfort` in the same file is the control: the fix must
    not be a special case that only markup survives.
    """
    found = parsed("[09 - Comfort Piece Settings]\n"
                   "# Setting type: Int32\n<color#00FFFF>Thor</color> Comfort = 2\n"
                   "# Setting type: Int32\nArmour Stand Comfort = 1\n")
    assert found["<color#00FFFF>Thor</color> Comfort"]["current"] == "2"
    assert found["Armour Stand Comfort"]["current"] == "1"


def test_the_file_header_is_not_the_first_description():
    """The created-by and GUID lines belong to the file, not to the first setting."""
    sections, guid, mod_name = schema.parse_config(AAA_CRAFTING)
    assert guid == "Azumatt.AzuAntiArthriticCrafting"
    assert mod_name == "AzuAntiArthriticCrafting"
    assert sections[0]["entries"][0]["description"] == (
        "If enabled, the crafting amount will be reset when the recipe is changed")


def test_a_list_a_range_and_neither_are_three_kinds():
    """`Acceptable values:` is a list, `Acceptable value range:` is a range, absent is none."""
    found = parsed("[S]\n"
                   "# Setting type: Toggle\n# Acceptable values: Off, On\nListed = On\n"
                   "# Setting type: Single\n# Acceptable value range: From 1 to 100\nRanged = 10\n"
                   "# Setting type: String\nOpen = anything\n")
    assert found["Listed"]["acceptable"] == {"kind": "list", "values": ["Off", "On"]}
    # Bounds stay raw tokens. Round-tripping "0.5" through a float is how a value the game
    # accepts becomes one it does not.
    assert found["Ranged"]["acceptable"] == {"kind": "range", "min": "1", "max": "100"}
    assert found["Open"]["acceptable"] == {"kind": "none"}


def test_a_negative_and_decimal_range_survives():
    """The low bound is matched non-greedily, so a minus sign or a point is not eaten."""
    found = parsed("[S]\n# Setting type: Single\n# Acceptable value range: From -0.5 to 2.25\nK = 1\n")
    assert found["K"]["acceptable"] == {"kind": "range", "min": "-0.5", "max": "2.25"}


def test_a_keybind_keeps_its_chord_and_its_key_list():
    """8 of 131 KeyboardShortcut entries declare the KeyCode enum, and 36 values are chords.

    A chord is never a member of that list, so the schema must carry both faithfully and
    leave the reconciling to the validator rather than normalising either one.
    """
    found = parsed("[S]\n## Pause it [Not Synced with Server]\n"
                   "# Setting type: KeyboardShortcut\n# Default value: G\n"
                   "# Acceptable values: None, Period, F, G, LeftShift\n"
                   "Pause Shortcut = Period + LeftShift\n")["Pause Shortcut"]
    assert found["current"] == "Period + LeftShift"
    assert found["acceptable"]["values"] == ["None", "Period", "F", "G", "LeftShift"]
    assert "multiple" not in found["acceptable"]


def test_the_flags_marker_makes_a_list_multiple():
    """212 keys in the live corpus carry it; without it "Swamp, Mountains" looks like junk."""
    found = parsed(
        "[S]\n# Setting type: Trader\n# Acceptable values: None, Haldor, Hildir\n"
        "# Multiple values can be set at the same time by separating them with , (e.g. Debug, Warning)\n"
        "Flags = None\n"
        "# Setting type: Toggle\n# Acceptable values: Off, On\nPlain = On\n")
    assert found["Flags"]["acceptable"]["multiple"] is True
    # The control: a plain list must NOT be marked multiple, or every radio becomes a
    # multi-select and "Off, On" becomes a legal value for a toggle.
    assert "multiple" not in found["Plain"]["acceptable"]


def test_a_value_containing_an_equals_sign_is_kept_whole():
    """Six values in the live corpus contain a second `=`; splitting on the last corrupts them."""
    assert parsed("[S]\nQuery = a=b&c=d\n")["Query"]["current"] == "a=b&c=d"


def test_an_empty_value_is_a_value():
    """1356 keys in the corpus are set to nothing. That is a setting, not a missing one."""
    assert parsed("[Debug]\nFilter = \n")["Filter"]["current"] == ""


def test_an_untyped_key_is_kept():
    """455 keys in this world carry no `# Setting type`. Dropping them hides real settings."""
    found = parsed("[Debug]\n# Local debug logging. Not synchronized.\nEnabled = false\n")
    assert found["Enabled"]["current"] == "false"
    assert "type" not in found["Enabled"]


def test_keys_before_any_section_are_kept():
    """BepInEx always writes a section and this corpus has none, but losing them is silent."""
    sections, _, _ = schema.parse_config("Loose = 1\n\n[Real]\nInside = 2\n")
    assert entries(sections)["Loose"]["current"] == "1"
    assert [section["name"] for section in sections] == ["", "Real"]


def test_crlf_and_a_bom_do_not_reach_the_values(tmp_path):
    """Both bite on this host: a CRLF file defeated an anchored grep on 2026-08-20."""
    path = tmp_path / "crlf.cfg"
    path.write_bytes("\ufeff[S]\r\n# Setting type: Boolean\r\nK = true\r\n".encode())
    sections, _, _ = schema.parse_config(schema.read_text(path))
    assert sections[0]["name"] == "S"
    assert entries(sections)["K"] == {"key": "K", "current": "true", "type": "Boolean",
                                     "acceptable": {"kind": "none"}}


# ---------------------------------------------------------------------------
# Whole-world assembly: sections, immutability, attribution, fingerprint.
# ---------------------------------------------------------------------------

def build_world(root, world="Hrafnheim", client=("flat",)):
    """A fleet with one world's server config tree and its profiles, as this host lays it out.

    Returns the config root, the profiles root and a REPO root holding a release-targets.json
    that names this world. The repo root is returned rather than defaulted because
    client_trees reads the real one otherwise: the live file names Hrafnheim and the profiles
    flat/vr/admin, so a test using those names would silently depend on repo data and change
    behaviour the day someone edits a release target.
    """
    config = root / world / "config_merged" / "bepinex"
    plugin = config / "plugins" / "AAA_Crafting"
    plugin.mkdir(parents=True)
    plugin.joinpath("AzuAntiArthriticCrafting.dll").write_bytes(b"MZ")
    # utf-8-sig on purpose: 26 of the 100 real manifests carry a BOM and fail strict utf-8.
    plugin.joinpath("manifest.json").write_bytes(json.dumps({"name": "AAA_Crafting"}).encode("utf-8-sig"))
    for name in client:
        profile = root / "profiles" / name
        (profile / "client-config").mkdir(parents=True)
        (profile / "profile-manifest.json").write_text(json.dumps({
            "schema_version": 2, "profile_name": name,
            "packages": [{"identifier": "Azumatt-AAA_Crafting", "version": "2.1.6"}],
        }))
    (config / "Azumatt.AzuAntiArthriticCrafting.cfg").write_text(AAA_CRAFTING)
    repo = root / "repo"
    repo.mkdir()
    (repo / "release-targets.json").write_text(json.dumps({
        "schema": 1,
        "flat": [{"world": world, "source_profile": name, "published_profile": f"{world}-{name}",
                  "valheim_vr": True, "audience": "player"} for name in client],
    }))
    return config, root / "profiles", repo


def test_a_config_is_attributed_through_the_filesystem(tmp_path):
    """GUID tail -> assembly -> plugin directory -> manifest -> profile identifier.

    None of those names is derivable from the config's own: the GUID is
    `Azumatt.AzuAntiArthriticCrafting`, the assembly `AzuAntiArthriticCrafting.dll`, the
    directory `AAA_Crafting` and the package `Azumatt-AAA_Crafting`.
    """
    _, profiles, repo = build_world(tmp_path)
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    assert result["schema"] == "world-config-schema/v1"
    assert result["world"] == "Hrafnheim"
    assert [record["file"] for record in result["files"]] == ["Azumatt.AzuAntiArthriticCrafting.cfg"]
    assert result["files"][0]["mod_identifier"] == "Azumatt-AAA_Crafting"
    assert result["files"][0]["mod_name"] == "AzuAntiArthriticCrafting"
    assert result["unattributed"] == []


def test_an_unattributable_config_is_named_not_guessed(tmp_path):
    """14 of this world's configs belong to mods with no plugin directory: removal leftovers.

    Reported by name with an empty identifier. Guessing an owner from the string would file a
    stranger's settings under a mod the admin does have.
    """
    config, profiles, repo = build_world(tmp_path)
    (config / "marcopogo.PlanBuild.cfg").write_text(
        "## Settings file was created by plugin PlanBuild v0.13.4\n"
        "## Plugin GUID: marcopogo.PlanBuild\n\n[General]\n# Setting type: Boolean\nK = true\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    orphan = next(item for item in result["files"] if item["file"] == "marcopogo.PlanBuild.cfg")
    assert orphan["mod_identifier"] == ""
    # The name still comes from the file's own header, so the page can group it honestly.
    assert orphan["mod_name"] == "PlanBuild"
    assert [item["file"] for item in result["unattributed"]] == ["marcopogo.PlanBuild.cfg"]


def test_a_config_in_a_subdirectory_is_attributed_by_that_directory(tmp_path):
    """Five real configs sit in subdirectories and carry no GUID header at all.

    `shudnal.ConditionalConfigSync/` is the only attribution those files have.
    """
    config, profiles, repo = build_world(tmp_path)
    owner = config / "plugins" / "ConditionalConfigSync"
    owner.mkdir()
    owner.joinpath("manifest.json").write_text(json.dumps({"name": "ConditionalConfigSync"}))
    nested = config / "shudnal.ConditionalConfigSync"
    nested.mkdir()
    (nested / "ConditionalConfigSync.Debug.cfg").write_text("[Debug]\nEnabled = false\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    record = next(item for item in result["files"]
                  if item["file"] == "shudnal.ConditionalConfigSync/ConditionalConfigSync.Debug.cfg")
    assert record["mod_name"] == "ConditionalConfigSync"


def test_immutable_is_a_section_fact_and_only_a_section_fact(tmp_path):
    """C5, as corrected from source. `[Immutable]` is a section NAME, not a BepInEx annotation.

    Exactly one section in the whole corpus is called that - ValheimVRMod's, holding 7 keys -
    and VHVR's createImmutableSettingWithOverride binds them normally and then overrides them
    from the command line at startup. So it means read-once-per-session, not unwritable, and
    the page's copy has to say "takes effect on restart" rather than implying the value cannot
    be set at all.

    There is NO key-level immutable annotation anywhere in the corpus, so the flag is emitted
    on the section and deliberately not copied onto its entries: the consumer folds the
    section flag in when it looks an entry up, and two copies of one fact are two things to
    drift.
    """
    config, profiles, repo = build_world(tmp_path)
    (config / "org.bepinex.plugins.valheimvrmod.cfg").write_text(
        "[Graphics]\n# Setting type: Boolean\nShowDamageText = true\n"
        "[Immutable]\n# Setting type: Boolean\nModEnabled = true\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    record = next(item for item in result["files"]
                  if item["file"] == "org.bepinex.plugins.valheimvrmod.cfg")
    by_name = {section["name"]: section for section in record["sections"]}
    assert by_name["Immutable"]["immutable"] is True
    # The control: the normal section beside it is untouched, so the flag is not global.
    assert by_name["Graphics"]["immutable"] is False
    # No entry claims it, in either section. Claiming it per key would assert an annotation
    # that does not exist in any config file on this host.
    assert all("immutable" not in entry
               for section in record["sections"] for entry in section["entries"])


def test_a_sections_verdict_needs_every_declaring_entry_to_agree(tmp_path):
    """The badge belongs on a section of 28 rows once, not 28 times - but only if it is true.

    A section where the entries disagree, or where none declared anything, gets neither flag:
    a majority vote there would invent a declaration no mod author made.
    """
    config, profiles, repo = build_world(tmp_path)
    (config / "mixed.cfg").write_text(
        "[All]\n## A [Synced with Server]\nOne = 1\n## B [Synced with Server]\nTwo = 2\n"
        "[None]\n## A [Not Synced with Server]\nOne = 1\n## B [Not Synced with Server]\nTwo = 2\n"
        "[Split]\n## A [Synced with Server]\nOne = 1\n## B [Not Synced with Server]\nTwo = 2\n"
        "[Silent]\nOne = 1\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    record = next(item for item in result["files"] if item["file"] == "mixed.cfg")
    verdict = {section["name"]: (section["synced"], section["client_side"])
               for section in record["sections"]}
    assert verdict == {"All": (True, False), "None": (False, True),
                       "Split": (False, False), "Silent": (False, False)}


def test_plugin_shipped_configs_are_not_settings(tmp_path):
    """A cfg inside plugins/ is the mod's own data, and 100 packages plus their DLLs live there."""
    config, profiles, repo = build_world(tmp_path)
    (config / "plugins" / "AAA_Crafting" / "shipped.cfg").write_text("[S]\nK = 1\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    assert [record["file"] for record in result["files"]] == ["Azumatt.AzuAntiArthriticCrafting.cfg"]


def test_the_backup_copies_the_publish_scripts_leave_are_not_configs(tmp_path):
    """21 `.cfg.before-*` files sit beside 18 real configs in one profile directory."""
    config, profiles, repo = build_world(tmp_path)
    (config / "Azumatt.WardIsLove.cfg.before-f4-20260817T065333Z").write_text("[S]\nK = 1\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    assert [record["file"] for record in result["files"]] == ["Azumatt.AzuAntiArthriticCrafting.cfg"]


def test_the_fingerprint_moves_when_a_config_does(tmp_path):
    """The staleness check the portal calls on every page view.

    Taken over size and mtime rather than content, so it stays two stats per file across 113
    of them. Configs change on this host without the portal seeing it, so a cache keyed on
    portal events alone would serve a stale schema.
    """
    config, _, _ = build_world(tmp_path)
    before = schema.fingerprint(config)
    assert len(before) == 64
    (config / "Azumatt.AzuAntiArthriticCrafting.cfg").write_text(AAA_CRAFTING + "Extra = 1\n")
    grown = schema.fingerprint(config)
    assert grown != before
    # A file appearing must move it too: a newly installed mod's settings are the common case.
    (config / "another.cfg").write_text("[S]\nK = 1\n")
    assert schema.fingerprint(config) != grown


def test_an_unreadable_config_is_named_never_skipped(tmp_path, monkeypatch):
    """A config that vanishes silently is indistinguishable from a mod with no settings."""
    config, profiles, repo = build_world(tmp_path)
    (config / "broken.cfg").write_text("[S]\nK = 1\n")
    real = schema.read_text

    def refuse(path):
        if path.name == "broken.cfg":
            raise OSError("Input/output error")
        return real(path)

    monkeypatch.setattr(schema, "read_text", refuse)
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    assert [item["file"] for item in result["unreadable"]] == ["broken.cfg"]
    assert "broken.cfg" not in [record["file"] for record in result["files"]]


def test_a_world_name_that_is_a_path_is_refused(tmp_path, monkeypatch):
    """The world reaches this tool from an HTTP route, so it must not address a parent."""
    monkeypatch.setenv("VALHEIM_ROOT", str(tmp_path))
    (tmp_path / "Hrafnheim").mkdir()
    for name in ("../etc", "a/b", "", "-flag"):
        with pytest.raises(schema.portal_paths.ConfigurationError):
            schema.resolve_world(name)
    # The control: a real world beside those still resolves, so the guard is not a blanket no.
    assert schema.resolve_world("Hrafnheim")[0] == tmp_path


# ---------------------------------------------------------------------------
# The union of both config sources, and the two facts that travel with each file.
# ---------------------------------------------------------------------------

def test_a_client_only_config_is_in_the_schema(tmp_path):
    """The reason the union exists.

    neuralyze.vrfixes.cfg is absent from config_merged in all four worlds, because the mod
    never runs server-side so the server never generated its metadata - and it is the file
    C3's own example names, holding the settings the operator actually edits. A schema built
    from the world tree alone cannot see it at all.
    """
    config, profiles, repo = build_world(tmp_path)
    (profiles / "flat" / "client-config" / "neuralyze.vrfixes.cfg").write_text(
        "[11 - Hover actions]\n## Which grip [Not Synced with Server]\n"
        "# Setting type: String\nModifier = RightGrip\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    record = next(item for item in result["files"] if item["file"] == "neuralyze.vrfixes.cfg")
    assert record["source"] == "client_profile"
    assert record["shipped"] is True
    assert record["sections"][0]["entries"][0]["key"] == "Modifier"
    # The control: the world-tree file beside it is untouched and is NOT marked shipped,
    # because no client tree carries it.
    other = next(item for item in result["files"]
                 if item["file"] == "Azumatt.AzuAntiArthriticCrafting.cfg")
    assert (other["source"], other["shipped"]) == ("config_merged", False)


def test_source_and_shipped_are_on_every_file(tmp_path):
    """Both unconditional, never omitempty.

    With omitempty a false `shipped` is indistinguishable from a cached payload written
    before the field existed, and the page would state "not in force" about a file it knows
    nothing about. Absence has to mean absence.
    """
    config, profiles, repo = build_world(tmp_path)
    (profiles / "flat" / "client-config" / "only-here.cfg").write_text("[S]\nK = 1\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    assert len(result["files"]) == 2
    for record in result["files"]:
        assert "source" in record and "shipped" in record
        assert record["source"] in {"config_merged", "client_profile", "both"}
        assert isinstance(record["shipped"], bool)


def test_a_file_in_both_trees_takes_the_world_trees_metadata(tmp_path):
    """Measured, not assumed: the world tree is a full dump, the client tree an overlay.

    org.bepinex.plugins.valheimvrmod.cfg is 145 keys across 7 sections in config_merged
    against 26 across 6 in profiles/vr/client-config-vr. Preferring the overlay would drop
    119 real settings, including the whole Immutable section.
    """
    config, profiles, repo = build_world(tmp_path)
    (config / "org.bepinex.plugins.valheimvrmod.cfg").write_text(
        "[Graphics]\n# Setting type: Boolean\nShowDamageText = true\n"
        "# Setting type: String\nShowAttackOutline = All\n"
        "[Immutable]\n# Setting type: Boolean\nModEnabled = true\n")
    (profiles / "flat" / "client-config" / "org.bepinex.plugins.valheimvrmod.cfg").write_text(
        "[Graphics]\nShowDamageText = false\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    matching = [item for item in result["files"]
                if item["file"] == "org.bepinex.plugins.valheimvrmod.cfg"]
    # Exactly one record. Two would give the page two answers for one key.
    assert len(matching) == 1
    record = matching[0]
    assert record["source"] == "both"
    assert record["shipped"] is True
    keys = [entry["key"] for section in record["sections"] for entry in section["entries"]]
    assert keys == ["ShowDamageText", "ShowAttackOutline", "ModEnabled"]
    assert any(section["immutable"] for section in record["sections"])


def test_the_richest_copy_wins_and_the_divergence_is_reported(tmp_path):
    """Two profiles ship DIFFERENT content under one basename, and publish order loses keys.

    Measured: neuralyze.vrfixes.cfg is 31 keys in profiles/vr and 30 in profiles/flat and
    profiles/admin, the extra one being LogShieldBlocks - a setting the operator spent the
    evening editing. republish copies flat first, so first-wins precedence drops exactly the
    key that matters, invisibly. The richest copy describes the file and the disagreement is
    reported rather than resolved in silence.
    """
    config, profiles, repo = build_world(tmp_path, client=("flat", "vr"))
    (profiles / "flat" / "client-config" / "neuralyze.vrfixes.cfg").write_text(
        "[9 - Profiling]\nProfileGameMethods = false\n")
    (profiles / "vr" / "client-config" / "neuralyze.vrfixes.cfg").write_text(
        "[9 - Profiling]\nLogShieldBlocks = true\nProfileGameMethods = false\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    record = next(item for item in result["files"] if item["file"] == "neuralyze.vrfixes.cfg")
    keys = [entry["key"] for section in record["sections"] for entry in section["entries"]]
    assert "LogShieldBlocks" in keys
    assert next(item for item in result["divergent"]
                if item["file"] == "neuralyze.vrfixes.cfg")["copies"] == [
        {"profile": "vr", "tree": "client-config", "keys": 2},
        {"profile": "flat", "tree": "client-config", "keys": 1}]


def test_identical_copies_are_not_reported_as_divergent(tmp_path):
    """The control for the test above: three matching copies are the normal case.

    Reporting those would drown the one real disagreement in noise.
    """
    config, profiles, repo = build_world(tmp_path, client=("flat", "vr"))
    for name in ("flat", "vr"):
        (profiles / name / "client-config" / "same.cfg").write_text("[S]\nK = 1\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    assert result["divergent"] == []


def test_a_world_with_no_release_target_ships_nothing(tmp_path):
    """shipped comes from the publish plan, never from an authority record.

    A world nothing publishes has no client trees, so no file can be claimed to reach a
    player. Guessing true here would silently promise delivery.
    """
    config, profiles, repo = build_world(tmp_path)
    (profiles / "flat" / "client-config" / "orphan.cfg").write_text("[S]\nK = 1\n")
    assert schema.client_trees("Hrafnheim", profiles, repo) != []
    assert schema.client_trees("Nowhere", profiles, repo) == []
    result = schema.world_schema(tmp_path, "Nowhere", profiles, repo)
    assert result["files"] == []
    assert result["sources"] == [str(tmp_path / "Nowhere" / "config_merged" / "bepinex")]


def test_the_fingerprint_moves_when_a_client_config_does(tmp_path):
    """A client tree edit must invalidate the cache, or the page serves a stale schema.

    This is not hypothetical: the operator hand-edited neuralyze.vrfixes.cfg repeatedly this
    evening, and a fingerprint over the world tree alone would have gone on matching.
    """
    config, profiles, repo = build_world(tmp_path)
    trees = schema.client_trees("Hrafnheim", profiles, repo)
    client = profiles / "flat" / "client-config" / "neuralyze.vrfixes.cfg"
    client.write_text("[S]\nModifier = RightGrip\n")
    before = schema.fingerprint(config, trees)
    client.write_text("[S]\nModifier = LeftGrip\nExtra = 1\n")
    assert schema.fingerprint(config, trees) != before
    # The control: the same tree unchanged hashes the same, so the check is not just noisy.
    assert schema.fingerprint(config, trees) == schema.fingerprint(config, trees)


def test_mod_name_falls_back_to_the_plugin_guid(tmp_path):
    """A client config has no plugin directory, no manifest and often no created-by header.

    neuralyze.vrfixes.cfg is exactly that, and an empty mod_name would leave the page unable
    to group it under anything. The stem IS the plugin's BepInEx GUID.
    """
    config, profiles, repo = build_world(tmp_path)
    (profiles / "flat" / "client-config" / "neuralyze.vrfixes.cfg").write_text("[S]\nK = 1\n")
    result = schema.world_schema(tmp_path, "Hrafnheim", profiles, repo)
    record = next(item for item in result["files"] if item["file"] == "neuralyze.vrfixes.cfg")
    assert record["mod_name"] == "neuralyze.vrfixes"
    assert record["mod_identifier"] == ""
