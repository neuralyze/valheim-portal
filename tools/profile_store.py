#!/usr/bin/env python3
"""Mod profiles as shared definitions, stored once, with servers linked to them.

Today a profile is a 2.1 GB directory inside one world, and four worlds hold four
unrelated copies with no record of which came from which. Changing a mod across the
fleet is four edits, and the copies drift: one world silently excludes VNEI, another
is missing a plugin the rest have.

This module is the model that replaces it:

* A profile lives once, at ``<fleet root>/profiles/<name>``, and belongs to no world.
  It is the single place a mod set is edited.
* A server *links* to a profile by name, in ``<world>/mods/.active-mod-profile`` -
  the file that already selects a profile today, now naming a shared one. Several
  servers may link to the same profile; editing it changes what every one of them
  runs at its next restart.
* A new profile is created empty or copied. A copy is independent from the moment
  it exists: that is the difference between copying and linking, and the reason a
  copy carries no reference to its source.
* A server is never created from another server. It is created, then linked.

``world_name`` is deliberately absent from a profile manifest written here. That
field is what made a profile belong to one world; keeping it would leave the old
model in the data while the directory layout claimed the new one.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

if __package__:
    from . import portal_paths
else:
    import portal_paths

# The name is used as a directory and reaches the shell scripts and the admin UI,
# so it is restricted to the same shape valheim_profile_catalog already enforces.
VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

LINK_FILE = ".active-mod-profile"
MANIFEST_NAME = "profile-manifest.json"
PROFILE_ROOT_ENVIRONMENT = "VALHEIM_PROFILE_ROOT"

MANIFEST_LISTS = (
    "packages", "client_only_packages", "disabled_packages", "custom_packages",
    "manual_server_packages", "excluded_packages",
)


class ProfileError(RuntimeError):
    """A profile operation was refused. The message names what to do instead."""


def profiles_root(fleet_root: Path | None = None) -> Path:
    """The one directory holding every profile.

    Beside the worlds rather than inside one, for the same reason the settings
    history lives there: it is shared, so it cannot belong to a world.
    """
    override = os.environ.get(PROFILE_ROOT_ENVIRONMENT, "")
    if override:
        if not override.startswith("/"):
            raise ProfileError(f"{PROFILE_ROOT_ENVIRONMENT} must be an absolute path: {override}")
        return Path(override)
    return (fleet_root or portal_paths.world_root()) / "profiles"


def _validate(name: str) -> str:
    if not VALID_NAME.fullmatch(name or ""):
        raise ProfileError(f"invalid profile name: {name!r}")
    return name


def profile_dir(name: str, root: Path | None = None) -> Path:
    return (root or profiles_root()) / _validate(name)


def manifest_path(name: str, root: Path | None = None) -> Path:
    return profile_dir(name, root) / MANIFEST_NAME


def profile_names(root: Path | None = None) -> list[str]:
    root = root or profiles_root()
    if not root.is_dir():
        return []
    return sorted(
        directory.name for directory in root.iterdir()
        if directory.is_dir() and not directory.is_symlink()
        and VALID_NAME.fullmatch(directory.name) and (directory / MANIFEST_NAME).is_file()
    )


def link_path(world_dir: Path) -> Path:
    return Path(world_dir) / "mods" / LINK_FILE


def linked_profile(world_dir: Path) -> str | None:
    """The profile this server runs, or None when it has not been linked yet."""
    path = link_path(world_dir)
    if not path.is_file():
        return None
    name = path.read_text(encoding="utf-8").strip()
    return name if VALID_NAME.fullmatch(name) else None


def link(world_dir: Path, name: str, root: Path | None = None) -> str:
    """Point one server at a profile.

    Refuses a name with no profile behind it: a link to nothing would deploy an
    empty mod set to a live server, which reads to players as "all mods gone".
    """
    _validate(name)
    if not manifest_path(name, root).is_file():
        raise ProfileError(f"no such profile: {name}")
    path = link_path(world_dir)
    if not path.parent.is_dir():
        raise ProfileError(f"not a world directory: {world_dir}")
    path.write_text(name + "\n", encoding="utf-8")
    return name


def worlds_in(fleet_root: Path) -> list[Path]:
    """Every directory that looks like a server's world, newest name order aside.

    A world is identified by holding a ``mods`` directory, which is what every
    provisioned world has and what ``world_backups`` and ``profiles`` do not.
    """
    fleet_root = Path(fleet_root)
    return sorted(
        directory for directory in fleet_root.iterdir()
        if directory.is_dir() and not directory.is_symlink() and (directory / "mods").is_dir()
    ) if fleet_root.is_dir() else []


def linked_servers(name: str, fleet_root: Path) -> list[str]:
    """The servers a change to ``name`` will reach at their next restart."""
    _validate(name)
    return [world.name for world in worlds_in(fleet_root) if linked_profile(world) == name]


def create(name: str, root: Path | None = None) -> Path:
    """A fresh profile with no mods."""
    root = root or profiles_root()
    destination = profile_dir(name, root)
    if destination.exists():
        raise ProfileError(f"profile already exists: {name}")
    for side in ("client", "server"):
        (destination / "manager-cache" / side / "BepInEx" / "plugins").mkdir(parents=True)
    (destination / "manual-mods").mkdir()
    (destination / "client-config").mkdir()
    manifest = {"schema_version": 2, "profile_name": name}
    manifest.update({key: [] for key in MANIFEST_LISTS})
    manifest_path(name, root).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination


def copy(source: str, name: str, root: Path | None = None) -> Path:
    """An independent duplicate of ``source``.

    No link back, no shared files, no ``copied_from``: an operator asking "what is
    this profile" must not be answered with a chain to somewhere else. The whole
    point of copy-versus-link is that a copy stops tracking its source here.
    """
    root = root or profiles_root()
    if not manifest_path(source, root).is_file():
        raise ProfileError(f"no such profile: {source}")
    destination = profile_dir(name, root)
    if destination.exists():
        raise ProfileError(f"profile already exists: {name}")
    shutil.copytree(profile_dir(source, root), destination, symlinks=False)
    manifest = json.loads(manifest_path(name, root).read_text(encoding="utf-8-sig"))
    manifest["profile_name"] = name
    manifest.pop("world_name", None)  # a shared profile belongs to no world
    manifest_path(name, root).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return destination


def delete(name: str, fleet_root: Path, root: Path | None = None) -> list[str]:
    """Remove a profile no server links to. Returns nothing useful; raises if linked.

    The existing tooling already refuses to delete the profile a world is running;
    with sharing, the same guard has to count every linked server rather than one.
    """
    root = root or profiles_root()
    if not profile_dir(name, root).is_dir():
        raise ProfileError(f"no such profile: {name}")
    linked = linked_servers(name, fleet_root)
    if linked:
        raise ProfileError(
            f"{name} is linked by {', '.join(linked)}; link those servers to another "
            f"profile before deleting this one"
        )
    shutil.rmtree(profile_dir(name, root))
    return linked


def describe(fleet_root: Path, root: Path | None = None) -> list[dict]:
    """One row per profile: what it holds and which servers it drives."""
    root = root or profiles_root()
    rows = []
    for name in profile_names(root):
        try:
            manifest = json.loads(manifest_path(name, root).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        packages = [*manifest.get("packages", []), *manifest.get("client_only_packages", [])]
        rows.append({
            "profile": name,
            "name": manifest.get("profile_name", name),
            "packages": len(packages),
            "disabled_packages": len(manifest.get("disabled_packages", [])),
            "custom_packages": len(manifest.get("custom_packages", [])),
            "servers": linked_servers(name, fleet_root),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profiles-root", type=Path, help="override the profile store")
    parser.add_argument("--fleet-root", type=Path, help="override the directory holding the worlds")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="profiles and the servers linked to each")
    listing.add_argument("--json", action="store_true")
    fresh = sub.add_parser("create", help="a new profile with no mods")
    fresh.add_argument("name")
    duplicate = sub.add_parser("copy", help="an independent copy of a profile")
    duplicate.add_argument("source")
    duplicate.add_argument("name")
    removal = sub.add_parser("delete", help="remove a profile no server links to")
    removal.add_argument("name")
    linking = sub.add_parser("link", help="point one server at a profile")
    linking.add_argument("world")
    linking.add_argument("name")
    shows = sub.add_parser("linked", help="which profile a server runs")
    shows.add_argument("world")
    args = parser.parse_args(argv)

    fleet = args.fleet_root or portal_paths.world_root()
    root = args.profiles_root or profiles_root(fleet)

    if args.command == "list":
        rows = describe(fleet, root)
        if args.json:
            print(json.dumps(rows, separators=(",", ":")))
        else:
            for row in rows:
                servers = ", ".join(row["servers"]) or "no servers"
                print(f'{row["profile"]}  {row["packages"]} packages  {servers}')
        return 0
    if args.command == "create":
        print(f"created={create(args.name, root)}")
        return 0
    if args.command == "copy":
        print(f"copied={args.source}->{copy(args.source, args.name, root)}")
        return 0
    if args.command == "delete":
        delete(args.name, fleet, root)
        print(f"deleted={args.name}")
        return 0
    world = fleet / args.world if not str(args.world).startswith("/") else Path(args.world)
    if args.command == "linked":
        print(linked_profile(world) or "unlinked")
        return 0
    print(f"linked={world.name}->{link(world, args.name, root)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except portal_paths.ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(portal_paths.EX_CONFIG)
    except ProfileError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
