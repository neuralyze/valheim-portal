#!/usr/bin/env python3
"""Transactionally create a portal-managed Valheim world directory."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
if __package__:
    from . import portal_paths, valheim_world, valheim_worldgen
else:
    import portal_paths
    import valheim_world
    import valheim_worldgen


TOOLS_ROOT = portal_paths.TOOLS_ROOT
MOD_CONTROLLER = TOOLS_ROOT / "valheim_mods.py"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DISPLAY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:-]{2,79}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9!@#$%^&*._+?-]{5,64}$")
INTERVALS = {"30m": "*/30 * * * *", "1h": "5 * * * *", "6h": "5 */6 * * *", "daily": "5 5 * * *"}
PRESETS = {"Normal", "Casual", "Easy", "Hard", "Hardcore", "Immersive", "Hammer"}


def valid_name(value: str) -> bool:
    return bool(NAME_RE.fullmatch(value))


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def occupied_ports() -> set[int]:
    used: set[int] = set()
    for env_path in portal_paths.world_root().glob("*/valheim.env"):
        values = parse_env(env_path)
        # CONTAINER_VALHEIM_PORT is the range actually published on the host.
        # SERVER_PORT only names the in-container port and is normally absent,
        # so reserving it alone left real host ranges free to be handed out.
        published = values.get("CONTAINER_VALHEIM_PORT", "")
        bounds = [part for part in published.split("-", 1) if part.isdigit()]
        if bounds:
            first = int(bounds[0])
            last = int(bounds[-1])
            if 0 < first <= last < 65536:
                used.update(range(first, last + 1))
        base_text = values.get("SERVER_PORT", "2456")
        if base_text.isdigit():
            base = int(base_text)
            used.update((base, base + 1))
        for key, default in (
            ("CONTAINER_STATUS_PORT", "31001"), ("SUPERVISOR_PORT", "19001"),
            ("DISCORD_BOT_PORT", "8877"),
        ):
            value = values.get(key, default).split("-", 1)[0]
            if value.isdigit():
                used.add(int(value))
    return used


def allocate(used: set[int], start: int, end: int) -> int:
    for port in range(start, end + 1):
        if port not in used:
            used.add(port)
            return port
    raise RuntimeError(f"no free host port in {start}-{end}")


def copy_selected_custom(source_world: Path, destination_world: Path, manifest: dict) -> None:
    source_root = (source_world / "mods" / "custom").resolve()
    destination_root = destination_world / "mods" / "custom"
    for entry in manifest.get("custom_packages", []):
        identifier = entry.get("id", "")
        relative = PurePosixPath(identifier)
        if not identifier or relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("template profile contains an invalid custom package identifier")
        source = (source_root / Path(*relative.parts)).resolve()
        if source_root not in source.parents or not source.is_file() or source.is_symlink():
            raise RuntimeError(f"template custom package is unavailable: {identifier}")
        destination = destination_root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def prepare_profile(stage: Path, world: str, profile: str, source_world_name: str, source_profile: str) -> Path:
    profiles = stage / "mods" / "profiles"
    destination = profiles / profile
    if source_world_name:
        if not valid_name(source_world_name) or not valid_name(source_profile):
            raise RuntimeError("invalid template profile")
        worlds_root = portal_paths.world_root()
        source_world = (worlds_root / source_world_name).resolve()
        source = (source_world / "mods" / "profiles" / source_profile).resolve()
        expected = (source_world / "mods" / "profiles").resolve()
        if worlds_root not in source_world.parents or expected not in source.parents or not (source / "profile-manifest.json").is_file():
            raise RuntimeError("template profile is unavailable")
        shutil.copytree(source, destination, symlinks=False)
        manifest = json.loads((destination / "profile-manifest.json").read_text())
        copy_selected_custom(source_world, stage, manifest)
    else:
        for side in ("client", "server"):
            (destination / "manager-cache" / side / "BepInEx" / "plugins").mkdir(parents=True)
        (destination / "manual-mods").mkdir(parents=True)
        (destination / "server-config" / "bepinex" / "config").mkdir(parents=True)
        (destination / "server-config" / "bepinex" / "plugins").mkdir(parents=True)
        manifest = {
            "schema_version": 1, "profile_name": profile, "world_name": world,
            "packages": [], "client_only_packages": [], "disabled_packages": [],
            "custom_packages": [], "manual_server_packages": [], "excluded_packages": [],
        }
    manifest["profile_name"] = profile
    manifest["world_name"] = world
    (destination / "profile-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (stage / "mods" / ".active-mod-profile").write_text(profile + "\n")
    return destination


def configure_player_limit(profile: Path, player_limit: int) -> None:
    if player_limit == 10:
        return
    subprocess.run([
        sys.executable, str(MOD_CONTROLLER), "--manifest", str(profile / "profile-manifest.json"),
        "add", "Azumatt-MaxPlayerCount",
    ], check=True)
    config = profile / "server-config" / "bepinex" / "config" / "Azumatt.MaxPlayerCount.cfg"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[1 - General]\n\nMaxPlayerCount = " + str(player_limit) + "\n")


def deploy_profile(stage: Path, profile: Path) -> None:
    merged = stage / "config_merged"
    server_config = profile / "server-config"
    if server_config.is_dir():
        shutil.copytree(server_config, merged, dirs_exist_ok=True)
    plugins = merged / "bepinex" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    for source in (
        profile / "manager-cache" / "server" / "BepInEx" / "plugins",
        profile / "manual-mods",
    ):
        if source.is_dir():
            shutil.copytree(source, plugins, dirs_exist_ok=True)


def quote_env(value: str) -> str:
    if "'" in value or "\n" in value or "\r" in value:
        raise RuntimeError("environment value contains unsupported characters")
    return "'" + value + "'"


def write_env(stage: Path, args: argparse.Namespace, password: str, ports: dict[str, int]) -> None:
    world_path = portal_paths.world_root() / args.world
    values = {
        "ENV_FILE": "valheim.env", "WORLD_NAME": args.world, "SERVER_NAME": args.server_name,
        "SERVER_PASS": password, "STEAMCMD_ARGS": "validate -beta public", "SERVER_PORT": str(args.port),
        "SERVER_PUBLIC": "1" if args.public else "0", "CROSSPLAY": "true" if args.crossplay else "false",
        "SERVER_ARGS": "-preset " + args.preset, "BEPINEX": "true", "DISCORD_BOT": "0",
        # Two host ports, matching the container's 2456-2457/udp pair. Docker
        # compose rejects the service when the two range sizes differ.
        "CONTAINER_VALHEIM_PORT": f"{args.port}-{args.port + 1}",
        "CONTAINER_STATUS_PORT": str(ports["status"]), "SUPERVISOR_PORT": str(ports["supervisor"]),
        "DISCORD_BOT_PORT": str(ports["discord"]),
        "BACKUPS": "true", "BACKUPS_CRON": INTERVALS[args.backup_interval],
        "BACKUPS_MAX_AGE": str(args.backup_age), "BACKUPS_MAX_COUNT": str(args.backup_count),
        "CONFIG_DIR": str(world_path / "config_merged"), "DATA_DIR": str(world_path / "data"),
    }
    content = "".join(f"{key}={quote_env(value)}\n" for key, value in values.items())
    path = stage / "valheim.env"
    path.write_text(content)
    path.chmod(0o600)


def prepare_world(stage: Path, args: argparse.Namespace) -> None:
    destination = stage / "config_merged" / "worlds_local"
    destination.mkdir(parents=True)
    if args.source_world:
        if not valid_name(args.source_world):
            raise RuntimeError("invalid source world")
        source_root = (portal_paths.world_root() / args.source_world / "config_merged" / "worlds_local").resolve()
        source_db = source_root / (args.source_world + ".db")
        source_fwl = source_root / (args.source_world + ".fwl")
        if not source_db.is_file() or source_db.is_symlink() or not source_fwl.is_file() or source_fwl.is_symlink():
            raise RuntimeError("source world save pair is unavailable")
        shutil.copy2(source_db, destination / (args.world + ".db"))
        valheim_world.parse(source_fwl)
        metadata = valheim_world.parse(source_fwl)
        metadata["name"] = args.world
        valheim_world.save(destination / (args.world + ".fwl"), metadata)
    # Seed mode deliberately leaves worlds_local empty. Valheim treats a .fwl whose .db
    # is missing as no world at all: it generates a fresh random seed and overwrites the
    # file, so a fabricated .fwl silently discards the operator's seed on first start.
    # The seed plugin deployed after the profile pins the seed instead, and the game
    # creates the .fwl/.db pair itself.


def container_group() -> int | None:
    """PGID the Valheim container chowns /config and /opt/valheim to."""
    values = parse_env(portal_paths.server_docker_dir() / "default.env")
    text = values.get("PGID", "1000")
    return int(text) if text.isdigit() else None


def share_with_container_group(stage: Path) -> None:
    """Make a freshly staged world writable by the container and the operator.

    mkdtemp gives the staging root 0700, which masks out any inherited ACL, and
    a world provisioned by the unprivileged agent is otherwise owned entirely by
    the agent. The container runs as PUID:PGID and chowns its own mounts on
    first start, so aligning the group and granting group write here is what
    lets the server save the world and lets an operator recover it before that
    first start ever happens. Group changes are best effort: only a member of
    the target group may chgrp, and root already owns the result outright.
    """
    group = container_group()
    for path in [stage, *stage.rglob("*")]:
        if path.is_symlink():
            continue
        if group is not None:
            try:
                os.chown(path, -1, group)
            except OSError:
                pass
        try:
            path.chmod(0o775 if path.is_dir() else 0o664)
        except OSError:
            pass


def provision(args: argparse.Namespace) -> None:
    password = os.environ.pop("PORTAL_SERVER_PASSWORD", "")
    if not valid_name(args.world) or not DISPLAY_RE.fullmatch(args.server_name) or not PASSWORD_RE.fullmatch(password):
        raise RuntimeError("invalid server identity or password")
    if not valid_name(args.profile) or args.preset not in PRESETS or args.backup_interval not in INTERVALS:
        raise RuntimeError("invalid profile, preset, or backup interval")
    if not 1024 <= args.port <= 65533 or not 1 <= args.player_limit <= 100 or not 1 <= args.backup_age <= 365 or not 1 <= args.backup_count <= 1000:
        raise RuntimeError("server numeric setting is out of range")
    if args.seed and (not valheim_world.valid_seed(args.seed) or args.source_world):
        raise RuntimeError("choose exactly one seed or source world")
    worlds_root = portal_paths.world_root()
    destination = worlds_root / args.world
    lock_path = worlds_root / ".portal-provision.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            raise RuntimeError("world directory already exists")
        used = occupied_ports()
        if any(port in used for port in (args.port, args.port + 1)):
            raise RuntimeError("game port range collides with a configured world")
        used.update((args.port, args.port + 1))
        ports = {
            "status": allocate(used, 30000, 39999), "supervisor": allocate(used, 40000, 44999),
            "discord": allocate(used, 20000, 29999),
        }
        staging_parent = Path(tempfile.mkdtemp(prefix=f".provision-{args.world}-", dir=worlds_root))
        stage = staging_parent / args.world
        try:
            stage.mkdir()
            (stage / "data").mkdir()
            (stage / "config" / "bepinex" / "plugins").mkdir(parents=True)
            os.symlink("bepinex", stage / "config" / "BepInEx")
            (stage / "config_merged" / "bepinex" / "plugins").mkdir(parents=True)
            (stage / "config_merged" / "bepinex" / "config").mkdir(parents=True)
            os.symlink("bepinex", stage / "config_merged" / "BepInEx")
            prepare_world(stage, args)
            profile = prepare_profile(stage, args.world, args.profile, args.template_world, args.template_profile)
            configure_player_limit(profile, args.player_limit)
            deploy_profile(stage, profile)
            if args.seed:
                # After deploy_profile, which copytrees the profile over config_merged.
                valheim_worldgen.deploy_seed_plugin(stage, args.seed)
            write_env(stage, args, password, ports)
            (stage / ".portal-managed").write_text("schema=1\n")
            share_with_container_group(stage)
            # The env file carries the server password, so it keeps group write
            # without becoming readable by everyone else.
            (stage / "valheim.env").chmod(0o660)
            os.replace(stage, destination)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
    print(f"provisioned={args.world} profile={args.profile} port={args.port}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision one Valheim world directory. Called by "
        "hostops/provision_valheim_server.sh, which the portal agent invokes "
        "with these fifteen positionals in this order.",
        epilog="The server password is read from the PORTAL_SERVER_PASSWORD "
        "environment variable so it never appears in argv.",
    )
    parser.add_argument("world", help="world name; also the directory name under the world root")
    parser.add_argument("server_name", help="display name shown in the Valheim server browser")
    parser.add_argument("port", type=int, help="base UDP game port; the query port is this plus one")
    parser.add_argument("public", choices=("true", "false"), help="list the server in the public browser")
    parser.add_argument("crossplay", choices=("true", "false"), help="enable PlayFab crossplay")
    parser.add_argument("player_limit", type=int, help="maximum concurrent players, 1 to 100")
    parser.add_argument("preset", help="Valheim world modifier preset: " + ", ".join(sorted(PRESETS)))
    parser.add_argument("backup_interval", help="automatic in-container backup cadence: " + ", ".join(INTERVALS))
    parser.add_argument("backup_age", type=int, help="age at which an automatic backup is pruned, 1 to 365")
    parser.add_argument("backup_count", type=int, help="automatic backups to keep, 1 to 1000")
    parser.add_argument("profile", help="name of the mod profile created in the new world")
    parser.add_argument("seed", help="world seed to pin, or empty to let Valheim generate one; not allowed with source_world")
    parser.add_argument("source_world", help="existing world whose save pair is copied in, or empty for a new world")
    parser.add_argument("template_world", help="existing world to copy the mod profile from, or empty to scaffold an empty profile")
    parser.add_argument("template_profile", help="profile name inside template_world; ignored when template_world is empty")
    args = parser.parse_args()
    args.public = args.public == "true"
    args.crossplay = args.crossplay == "true"
    provision(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except portal_paths.ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(portal_paths.EX_CONFIG)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
