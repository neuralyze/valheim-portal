#!/usr/bin/env python3
"""The typed schema of a world's BepInEx settings, so the portal never guesses a widget.

A `.cfg` file is the only machine-readable description of a mod's settings that
exists on this host. BepInEx writes a comment block above every key giving the
setting's type, its default, and - when the mod declared one - the values it will
accept. This module turns that corpus into `world-config-schema/v1`, which is
what the portal's settings manager renders and what the authority store
validates a stored value against.

Why the SERVER's config tree and not the profiles' client-config trees
---------------------------------------------------------------------
Measured on this host, 2026-08-21:

* ``<world>/config_merged/bepinex``: 108 ``.cfg`` files, 107 of them carrying
  ``# Setting type`` comments.
* ``profiles/{flat,vr,admin}/client-config``: 18 ``.cfg`` files, 12 of them
  carrying any metadata at all.

The client-config trees are not a corpus of a client's settings. They are the
handful of files an operator has hand-customised, and six of the eighteen carry
no metadata whatsoever, so a schema built from them would be blind to
essentially every setting the page is supposed to show. They are also
per-PROFILE and shared by all four worlds - byte-identical between `flat` and
`admin`, differing only in `neuralyze.vrfixes.cfg` between `flat` and `vr` - so
they cannot express a per-world value even in principle.

The server tree is per-world, and it is where the plugins themselves wrote their
metadata out. It is also, measurably, not clean: fourteen of its config files
belong to mods with no plugin directory and no assembly anywhere under
``plugins/`` - leftovers from removals. That is why attribution is reported
rather than assumed, and why an unattributable file is named in the payload's
``unattributed`` list instead of being silently dropped or silently given an
owner.

What the comment block actually looks like
------------------------------------------
Metadata lines carry ONE ``#``; only the human description uses ``##``. This is
worth stating because it was assumed the other way round when this feature was
scoped, and an anchored match on ``##`` finds nothing at all: Hrafnheim has
17014 ``# Setting type:`` lines and zero ``## Setting type:`` lines.

    ## Personal hotkey to toggle a ward on which you're permitted on/off [Not Synced with Server]
    # Setting type: KeyboardShortcut
    # Default value: G
    # Acceptable values: None, Backspace, Tab, ...
    WardHotKey = G

A list and a range are written differently, and both forms are present here:

    # Acceptable values: Off, On                  -> kind "list"
    # Acceptable value range: From 1 to 100       -> kind "range"

Bounds are kept as the raw tokens rather than parsed into numbers. The corpus
holds Single, Int32, UInt32, Double and mod-defined types under the same
``range`` form, and round-tripping "0.5" through a float and back is how a value
the game accepts turns into one it does not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

if __package__:
    from . import portal_paths, profile_store
else:
    import portal_paths
    import profile_store

SCHEMA = "world-config-schema/v1"
STATE_SCHEMA = "world-config-schema-state/v1"

# Same shape hostops/portal_world_config_schema.sh enforces, repeated here because the
# tool is also run by hand and must not be reachable with a traversal in the world name.
VALID_WORLD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

SETTING_TYPE = "# Setting type: "
DEFAULT_VALUE = "# Default value: "
ACCEPTABLE_LIST = "# Acceptable values: "
ACCEPTABLE_RANGE = "# Acceptable value range: "

# BepInEx's flags-enum marker, emitted verbatim above a key whose value may be a
# comma-separated set. Present 212 times in Hrafnheim and byte-identical every time, so it
# is matched as a literal prefix: without it, "Swamp, Mountains" (legal for a flags enum)
# is indistinguishable from "Off, On" (nonsense for a plain toggle).
MULTIPLE_MARKER = "# Multiple values can be set at the same time by separating them with"

# "From 1 to 100". Non-greedy on the low bound so a negative or decimal bound survives.
RANGE = re.compile(r"^From (.+?) to (.+)$")

PLUGIN_HEADER = re.compile(r"^## Plugin GUID: (.+)$")
CREATED_BY = re.compile(r"^## Settings file was created by plugin (.+?)(?: v[^\s]+)?\s*$")

# A section literally named Immutable. It is a section NAME chosen by a mod author, not a
# BepInEx annotation: ValheimVRMod puts its startup-only settings there and they need a
# client restart to take effect, so the portal must render them read-only (C5). The whole
# corpus contains exactly one such section, org.bepinex.plugins.valheimvrmod.cfg:348.
IMMUTABLE_SECTION = "immutable"

# The four spellings of the ServerSync annotation found on this host. The trap is that
# "[Not Synced with Server]" CONTAINS "Synced with Server", so a substring test marks 224
# not-synced entries as synced - which is exactly the lying-UI failure C4 is about, since a
# synced key is overwritten by the server in memory and can never honour a client override.
# Negative forms are therefore tested first, and both are stripped from the description.
SYNCED_MARKERS = ("[Synced with Server]", "[Synced with server]")
NOT_SYNCED_MARKERS = ("[Not Synced with Server]", "[Not synchronized with server]")


def read_text(path: Path) -> str:
    """A config file's text, BOM-tolerant and with line endings normalised.

    Both bite here. 26 of the 100 plugin manifests under a world carry a UTF-8 BOM, and the
    config files are frequently CRLF - a CRLF file defeated an anchored grep on 2026-08-20,
    and a trailing carriage return would otherwise end up inside every parsed value.
    """
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def squash(text: str) -> str:
    """A name reduced to letters and digits, for comparing across naming conventions.

    ``AAA_Crafting``, ``aaa-crafting`` and ``AAACrafting`` are one mod written three ways by
    Thunderstore, the plugin directory and the assembly.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


def split_synced(description: str) -> tuple[str, bool | None]:
    """The description with its ServerSync annotation removed, and what it said.

    Three states, and the caller emits them as two separate flags rather than one boolean.
    A tri-state carried by the ABSENCE of a json key does not survive the Go side, where
    `Synced bool` decodes an absent key and an explicit false identically - so "the mod said
    client-side" would become indistinguishable from "the mod said nothing", and 224 keys
    whose author explicitly declared them client-side are the strongest client_default
    candidates in the corpus. Losing that distinction is not cosmetic.

    None means the mod said nothing, which is not the same as saying "not synced": a mod
    that never wired ServerSync up leaves no annotation, and so does a mod that did and
    forgot to mention it. Only an explicit annotation is evidence.
    """
    for marker in NOT_SYNCED_MARKERS:
        if description.endswith(marker):
            return description[: -len(marker)].strip(), False
    for marker in SYNCED_MARKERS:
        if description.endswith(marker):
            return description[: -len(marker)].strip(), True
    return description, None


def parse_acceptable(listed: str | None, ranged: str | None, multiple: bool) -> dict:
    """The `acceptable` object for one entry.

    Exactly one of the three kinds, so the page can switch on kind alone. It has to: the
    corpus holds over a hundred distinct `Setting type` tokens, most of them mod-defined
    enums, and switching a widget on the type would need a case for every one of them.
    """
    if listed is not None:
        values = [value.strip() for value in listed.split(",")]
        acceptable = {"kind": "list", "values": [value for value in values if value]}
        if multiple:
            acceptable["multiple"] = True
        return acceptable
    if ranged is not None:
        bounds = RANGE.match(ranged.strip())
        if bounds:
            acceptable = {"kind": "range", "min": bounds.group(1).strip(), "max": bounds.group(2).strip()}
            if multiple:
                acceptable["multiple"] = True
            return acceptable
        # A range comment in a form this host has never produced. Reporting "none" would
        # claim the mod declared no bounds, which is a different and false statement, so the
        # raw text is carried through for a human to read.
        return {"kind": "range", "text": ranged.strip()}
    return {"kind": "none"}


def parse_config(text: str) -> tuple[list[dict], str, str]:
    """The sections of one config file, plus its plugin GUID and self-reported mod name.

    The pending comment block is discarded at a section header, which is what keeps the
    file's own two-line header (created-by, plugin GUID) from being read as the description
    of the first key in the first section.

    A key with no preceding metadata is kept with its type omitted. 415 of Hrafnheim's keys
    are in that state - mods that wrote a value without a Bind call, or hand-edited files -
    and dropping them would make 415 real settings uneditable.
    """
    guid = ""
    mod_name = ""
    sections: list[dict] = []
    # Keys before any section header. BepInEx always writes a section and this corpus has
    # none, but a hand-edited file could, and losing them silently is the failure mode here.
    current: dict = {"name": "", "entries": []}
    description: list[str] = []
    setting_type = default = listed = ranged = None
    multiple = False

    def reset() -> None:
        nonlocal description, setting_type, default, listed, ranged, multiple
        description = []
        setting_type = default = listed = ranged = None
        multiple = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # The kind of a line is decided from its LEADING character alone, before any split,
        # which is what both reference parsers do (Gale de.rs:190-200, r2modman
        # ConfigUtils.ts:73-76). Searching for a '#' anywhere in the line instead would turn
        # the real header `[<color#00FFFF>Thor</color>]` (Azumatt.WardIsLove.cfg:4) into
        # `[<color`, which is not a header at all: the section silently disappears and its
        # keys attach to whatever came before. One such header and two such key names exist
        # here, so it is one file's blast radius from a three-character mistake.
        #
        # First '[' to LAST ']', taken verbatim - BepInEx's own rule - so markup inside a
        # section name survives to be the grouping identity C1 keys on.
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) > 2:
            if current["entries"] or current["name"]:
                sections.append(current)
            current = {"name": stripped[1:-1], "entries": []}
            reset()
            continue
        if stripped.startswith("##"):
            header = PLUGIN_HEADER.match(stripped)
            if header:
                guid = header.group(1).strip()
                continue
            created = CREATED_BY.match(stripped)
            if created and not sections and not current["entries"]:
                mod_name = created.group(1).strip()
                continue
            description.append(stripped[2:].strip())
            continue
        if stripped.startswith("#"):
            if stripped.startswith(SETTING_TYPE):
                setting_type = stripped[len(SETTING_TYPE):].strip()
            elif stripped.startswith(DEFAULT_VALUE):
                default = stripped[len(DEFAULT_VALUE):].strip()
            elif stripped.startswith(ACCEPTABLE_LIST):
                listed = stripped[len(ACCEPTABLE_LIST):]
            elif stripped.startswith(ACCEPTABLE_RANGE):
                ranged = stripped[len(ACCEPTABLE_RANGE):]
            elif stripped.startswith(MULTIPLE_MARKER):
                multiple = True
            continue
        if "=" not in line:
            continue
        # The value keeps everything right of the FIRST '=' and is stripped only at its
        # edges. Six values in the corpus contain a further '=', and splitting on the last
        # one would corrupt them.
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            reset()
            continue
        text_description, synced = split_synced(" ".join(description).strip())
        entry = {"key": key, "current": value.strip()}
        if setting_type:
            entry["type"] = setting_type
        if default is not None:
            entry["default"] = default
        if text_description:
            entry["description"] = text_description
        entry["acceptable"] = parse_acceptable(listed, ranged, multiple)
        # Two flags, never one. `synced` true means the mod annotated the key
        # "[Synced with Server]", so the server overwrites it in memory at runtime and a
        # client override is a lie (C4). `client_side` true means the mod annotated it
        # "[Not Synced with Server]", which is a positive statement that an override WILL
        # hold - the best client_default candidates there are. Neither flag means the mod
        # said nothing, and that is a third state: silence is not a declaration either way.
        if synced:
            entry["synced"] = True
        elif synced is False:
            entry["client_side"] = True
        current["entries"].append(entry)
        reset()

    if current["entries"] or current["name"]:
        sections.append(current)
    return sections, guid, mod_name


class Attribution:
    """Which mod owns a config file, decided from the filesystem rather than from its name.

    A config's basename is the plugin's BepInEx GUID: across the 106 files that carry a
    ``## Plugin GUID:`` header the header and the basename agree every single time, zero
    mismatches. The GUID is not the mod's Thunderstore identifier though, and the
    relationship between the two is not derivable from the string -
    ``Azumatt.AzuAntiArthriticCrafting`` ships as ``Azumatt-AAA_Crafting`` in a directory
    called ``AAA_Crafting`` holding ``AzuAntiArthriticCrafting.dll``.

    So the chain is walked through real files: GUID tail -> assembly -> plugin directory ->
    that directory's manifest -> the profile manifest entry whose install name matches. Each
    rung is an exact match after squashing; nothing is prefix-matched or guessed. A file that
    comes out the far end unresolved is reported, because the usual reason is real and worth
    seeing - the mod is not installed in this world and its config is a leftover.
    """

    def __init__(self, plugins: Path, profiles: Path):
        self.by_dll: dict[str, set[str]] = {}
        self.by_name: dict[str, set[str]] = {}
        self.manifest_name: dict[str, str] = {}
        self.identifier: dict[str, str] = {}
        self._index_plugins(plugins)
        self._index_profiles(profiles)

    def _index_plugins(self, plugins: Path) -> None:
        if not plugins.is_dir():
            return
        for entry in sorted(plugins.iterdir()):
            if not entry.is_dir() or entry.is_symlink():
                continue
            self.by_name.setdefault(squash(entry.name), set()).add(entry.name)
            manifest = entry / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                # utf-8-sig: 26 of the 100 manifests here carry a BOM and fail strict utf-8.
                declared = json.loads(read_text(manifest)).get("name")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(declared, str) and declared:
                self.manifest_name[entry.name] = declared
                self.by_name.setdefault(squash(declared), set()).add(entry.name)
        for assembly in plugins.rglob("*.dll"):
            top = assembly.relative_to(plugins).parts[0]
            # A loose DLL dropped straight into plugins/ is its own plugin, with no manifest.
            top = top[:-4] if top.lower().endswith(".dll") else top
            self.by_dll.setdefault(assembly.stem.lower(), set()).add(top)

    def _index_profiles(self, profiles: Path) -> None:
        if not profiles.is_dir():
            return
        for name in profile_store.profile_names(profiles):
            try:
                manifest = json.loads(read_text(profile_store.manifest_path(name, profiles)))
            except (OSError, ValueError, profile_store.ProfileError):
                continue
            if not isinstance(manifest, dict):
                continue
            for key in ("packages", "client_only_packages", "manual_server_packages"):
                for item in manifest.get(key) or []:
                    identifier = item.get("identifier") if isinstance(item, dict) else item
                    if not isinstance(identifier, str) or "-" not in identifier:
                        continue
                    # package_install_name's rule: the owner prefix up to the FIRST hyphen is
                    # stripped, so Azumatt-AAA_Crafting installs as AAA_Crafting.
                    self.identifier.setdefault(squash(identifier.split("-", 1)[1]), identifier)

    def _directory(self, guid: str, mod_name: str, container: str) -> str:
        # A mod that writes several config files puts them in a subdirectory named after
        # itself - shudnal.ConditionalConfigSync/ and ItemStacksRewrite/ beside the flat
        # files - and for those five files that directory is the ONLY attribution source
        # they have: none carries a "Plugin GUID" header. The tail is tried as well as the
        # whole token because the directory is sometimes named with the GUID rather than the
        # plugin.
        for token in (guid, container):
            if not token:
                continue
            tail = token.rsplit(".", 1)[-1]
            for candidates in (self.by_dll.get(tail.lower()),
                               self.by_name.get(squash(tail)),
                               self.by_name.get(squash(token))):
                if candidates and len(candidates) == 1:
                    return next(iter(candidates))
        if mod_name:
            candidates = self.by_name.get(squash(mod_name))
            if candidates and len(candidates) == 1:
                return next(iter(candidates))
        return ""

    def resolve(self, guid: str, mod_name: str, container: str = "") -> tuple[str, str]:
        """(identifier, name) for a config, either half possibly empty.

        The name is preferred from the file's own ``created by plugin`` header, because that
        is the mod telling us what it calls itself - 106 of 108 files have it. The identifier
        can only come from a manifest, so it is empty for a config whose mod is installed by
        hand or not installed at all. C1 allows an empty identifier for exactly this case,
        and an empty one is reported in ``unattributed`` rather than filled with a guess.
        """
        directory = self._directory(guid, mod_name, container)
        identifier = ""
        if directory:
            identifier = (self.identifier.get(squash(directory))
                          or self.identifier.get(squash(self.manifest_name.get(directory, "")))
                          or "")
        if not identifier and mod_name:
            identifier = self.identifier.get(squash(mod_name)) or ""
        name = mod_name or self.manifest_name.get(directory, "") or directory or container
        return identifier, name


def config_files(config_root: Path) -> list[Path]:
    """Every ``.cfg`` under a world's server config tree, plugin-shipped ones excluded.

    ``plugins/`` holds the packages themselves, and a cfg inside one belongs to the mod's own
    data rather than to the operator. The ``.cfg.before-*`` copies the publish scripts leave
    behind are excluded by the suffix test, which is deliberate: there are 21 of them beside
    18 real configs in one profile directory alone.
    """
    if not config_root.is_dir():
        return []
    plugins = config_root / "plugins"
    found = []
    for path in config_root.rglob("*.cfg"):
        if not path.is_file() or path.is_symlink() or plugins in path.parents:
            continue
        found.append(path)
    return sorted(found, key=lambda path: str(path.relative_to(config_root)).lower())


def fingerprint(config_root: Path) -> str:
    """The staleness check: every config's path, size and mtime, hashed.

    Taken over the inputs rather than the parsed output, on the same reasoning as the mod
    catalog's: the portal asks for this on every page view, so it must stay two stats per
    file. Configs are edited on this host by scripts the portal never sees, so pure event
    invalidation would serve a stale schema.
    """
    rows = []
    for path in config_files(config_root):
        stat = path.stat()
        rows.append(f"{path.relative_to(config_root)}\t{stat.st_size}\t{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def world_schema(world_root: Path, world: str, profiles: Path) -> dict:
    """`world-config-schema/v1` for one world.

    ``generated_at`` is omitted on purpose. It would change on every rebuild while the portal
    caches this payload keyed on a fingerprint of the inputs, so a timestamp in the body
    would make two identical schemas compare unequal for no reason. The fingerprint is the
    freshness signal and the state mode returns it.
    """
    config_root = world_root / world / "config_merged" / "bepinex"
    attribution = Attribution(config_root / "plugins", profiles)
    files = []
    unattributed = []
    unreadable = []
    for path in config_files(config_root):
        relative = str(path.relative_to(config_root))
        try:
            sections, guid, mod_name = parse_config(read_text(path))
        except OSError as error:
            # Named, never skipped. A config that vanishes from the page without a word is
            # indistinguishable from a mod that has no settings.
            unreadable.append({"file": relative, "reason": str(error)})
            continue
        container = Path(relative).parent.name
        identifier, name = attribution.resolve(guid or Path(relative).stem, mod_name, container)
        record = {"file": relative, "mod_identifier": identifier, "mod_name": name, "sections": []}
        for section in sections:
            # `immutable` exists at SECTION level only, because that is the only level the
            # corpus supports: there is no key-level immutable annotation anywhere in it. The
            # flag is a section literally named Immutable, which is not a BepInEx annotation
            # at all but a name one mod chose - VHVR's createImmutableSettingWithOverride
            # binds those keys normally and then overrides them from the command line at
            # startup. So it means read-once-per-session, not unwritable: the page should
            # render them read-only and say the value takes effect on restart, rather than
            # implying it cannot be set. Deliberately NOT copied onto each entry - the
            # consumer folds the section flag in when it looks an entry up, and a second copy
            # of one fact is a second thing to drift.
            immutable = section["name"].strip().lower() == IMMUTABLE_SECTION
            # ServerSync mods annotate per key and write nothing on the section header, so a
            # section-level verdict has to be derived from its entries. An entry declares
            # nothing unless it carries one of the two flags, so the verdict is taken over
            # the entries that SPOKE: a section is synced when all of them said synced and at
            # least one did, and client_side when all of them said client_side. A section
            # where the two disagree, or where nobody spoke, gets neither - which keeps the
            # third state intact at section level too, rather than letting a majority vote
            # invent a declaration no mod author made.
            declared = [bool(item.get("synced")) for item in section["entries"]
                        if item.get("synced") or item.get("client_side")]
            record["sections"].append({
                "name": section["name"],
                "synced": bool(declared) and all(declared),
                "client_side": bool(declared) and not any(declared),
                "immutable": immutable,
                "entries": section["entries"],
            })
        if not identifier:
            unattributed.append({"file": relative, "guid": guid, "mod_name": name})
        files.append(record)
    return {
        "schema": SCHEMA,
        "world": world,
        "source": str(config_root),
        "files": files,
        "unattributed": unattributed,
        "unreadable": unreadable,
    }


def resolve_world(world: str) -> tuple[Path, Path]:
    if not VALID_WORLD.match(world or ""):
        raise portal_paths.ConfigurationError(f"invalid world name: {world!r}")
    root = portal_paths.world_root()
    if not (root / world).is_dir():
        raise portal_paths.ConfigurationError(f"no such world under {root}: {world}")
    return root, profile_store.profiles_root(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The typed schema of a world's BepInEx settings.")
    parser.add_argument("command", choices=("extract",))
    parser.add_argument("--world", required=True)
    parser.add_argument("--state", action="store_true",
                        help="Print only the fingerprint of the world's config tree, parsing nothing.")
    args = parser.parse_args(argv)
    try:
        root, profiles = resolve_world(args.world)
    except portal_paths.ConfigurationError as error:
        print(error, file=sys.stderr)
        return portal_paths.EX_CONFIG
    config_root = root / args.world / "config_merged" / "bepinex"
    if args.state:
        # The cheap half, called on every page view: stat each file and hash. No parsing.
        print(json.dumps({
            "schema": STATE_SCHEMA,
            "world": args.world,
            "fingerprint": fingerprint(config_root),
            "files": len(config_files(config_root)),
        }, separators=(",", ":")))
        return 0
    print(json.dumps(world_schema(root, args.world, profiles), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
