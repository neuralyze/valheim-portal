#!/usr/bin/env python3
"""Create or reset a Valheim world on a chosen seed, using the game's own generator.

A dedicated server takes no seed argument, and Valheim's World.LoadWorld runs
World.CheckDbFile(): a .fwl whose .db is missing is not a world at all, so
World.GetCreateWorld calls World.GenerateSeed() and overwrites the .fwl with a
fresh random seed. Handing the server a fabricated .fwl therefore loses the seed
silently. The NeuralyzeWorldSeed plugin patches GenerateSeed instead, so the game
creates the world itself - correct seed, pristine database, nothing stale.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
if __package__:
    from . import portal_paths, valheim_world
else:
    import portal_paths
    import valheim_world


TOOLS_ROOT = portal_paths.TOOLS_ROOT
HOSTOPS_ROOT = portal_paths.HOSTOPS_ROOT
PLUGIN_NAME = "NeuralyzeWorldSeed"
PLUGIN_SOURCE = TOOLS_ROOT / "worldseed" / (PLUGIN_NAME + ".dll")
CONFIG_NAME = "neuralyze.worldseed.cfg"
WORLD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SEED_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
SEED_KEY_RE = re.compile(r"^\s*ForcedSeedName\s*=\s*(\S*)\s*$", re.MULTILINE)
POLL_INTERVAL = 5
CONFIG_HEADER = """## Neuralyze World Seed - the seed name Valheim uses when it CREATES this world.
## Valheim discards a .fwl whose .db is missing and generates a random seed in its
## place, which is how a reset silently replaces a world. Pinning the name here makes
## that path rebuild this world instead. Creation only; an existing save is untouched.

[World]

"""


def valid_world(value: str) -> bool:
    return bool(WORLD_RE.fullmatch(value))


def valid_seed(value: str) -> bool:
    return bool(SEED_RE.fullmatch(value))


def saves_dir(world_root: Path) -> Path:
    return world_root / "config_merged" / "worlds_local"


def config_path(world_root: Path) -> Path:
    # Flat in bepinex/, not bepinex/config/: that is where this server's BepInEx reads
    # plugin configuration from, proven by the plugin logging the forced seed on start.
    return world_root / "config_merged" / "bepinex" / CONFIG_NAME


def plugin_path(world_root: Path) -> Path:
    return world_root / "config_merged" / "bepinex" / "plugins" / PLUGIN_NAME / (PLUGIN_NAME + ".dll")


def share(path: Path) -> None:
    """The container runs as its own uid, so group access is what lets it read this."""
    try:
        path.chmod(0o775 if path.is_dir() else 0o664)
    except OSError:
        pass


def pinned_seed(world_root: Path) -> str:
    try:
        match = SEED_KEY_RE.search(config_path(world_root).read_text())
    except (OSError, UnicodeDecodeError):
        return ""
    return match.group(1) if match else ""


def write_seed_config(world_root: Path, seed: str) -> None:
    path = config_path(world_root)
    valheim_world.atomic_write(path, (CONFIG_HEADER + f"ForcedSeedName = {seed}\n").encode("utf-8"))
    share(path)


def deploy_seed_plugin(world_root: Path, seed: str) -> None:
    if not valid_seed(seed):
        raise RuntimeError("seed must contain 1 to 64 letters or digits")
    if not PLUGIN_SOURCE.is_file():
        raise RuntimeError(f"world seed plugin is missing: {PLUGIN_SOURCE}")
    destination = plugin_path(world_root)
    valheim_world.atomic_write(destination, PLUGIN_SOURCE.read_bytes())
    share(destination)
    share(destination.parent)
    write_seed_config(world_root, seed)


def archive_world_files(world_root: Path, tag: str = "worldgen") -> str | None:
    """Move every save file for this world aside. Never deletes; returns the directory."""
    source = saves_dir(world_root)
    files = sorted(source.glob(world_root.name + "*")) if source.is_dir() else []
    if not files:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive = world_root / "config_merged" / f"world-archive-{stamp}-{tag}"
    attempt = 1
    while archive.exists():
        archive = world_root / "config_merged" / f"world-archive-{stamp}-{tag}-{attempt}"
        attempt += 1
    archive.mkdir(parents=True)
    share(archive)
    for path in files:
        os.replace(path, archive / path.name)
    return archive.name


def read_seed(fwl: Path) -> tuple[str, int]:
    metadata = valheim_world.parse(fwl)
    return metadata["seed"], metadata["seed_value"]


def run_script(name: str, world: str) -> None:
    script = HOSTOPS_ROOT / name
    if not script.is_file():
        raise RuntimeError(f"missing host script: {script}")
    result = subprocess.run([str(script), world], capture_output=True, text=True)
    # Both lifecycle scripts chat on stdout; stdout here belongs to the JSON result.
    sys.stderr.write(result.stdout + result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"{name} failed for {world} with status {result.returncode}")


def recreate(world: str, seed: str) -> dict:
    """Stop, archive, pin the seed, start. Perform the operation; do not judge the result.

    Every failure this tool had came from the success check, never from the generation. Three
    different checks were wrong - a .db that Valheim does not write for twenty minutes, a stop five
    seconds after start, and "Game server connected", which is a Steam event that fires before the
    world even loads. Each wrong check then triggered a rollback that destroyed a world which had in
    fact generated perfectly, on the correct seed.

    So there is no check and no rollback. Nothing is deleted - the previous save is archived - so
    there is no state to roll back to. Use `status` to inspect the result; being read-only, it can be
    wrong without costing anything.

    The .db is deliberately not required. The seed-forcing plugin makes a .fwl-without-.db harmless:
    Valheim would re-create the world, and the pin means it re-creates the SAME one.
    """
    if not valid_world(world):
        raise RuntimeError("invalid world name")
    if not valid_seed(seed):
        raise RuntimeError("seed must contain 1 to 64 letters or digits")
    world_root = portal_paths.world_root() / world
    if not world_root.is_dir() or world_root.is_symlink():
        raise RuntimeError(f"world directory is unavailable: {world_root}")
    if not (world_root / "valheim.env").is_file():
        raise RuntimeError(f"world {world} has no server environment")

    run_script("stop_valheim_server.sh", world)
    archive = archive_world_files(world_root)
    deploy_seed_plugin(world_root, seed)
    run_script("start_valheim_server.sh", world)
    return {
        "world": world, "requested_seed": seed, "archived_previous_world_to": archive,
        "server": "starting",
        "note": "world building takes several minutes; run `status` to see the result",
    }


def status(world: str) -> dict:
    """Read-only report on a world. Touches nothing."""
    if not valid_world(world):
        raise RuntimeError("invalid world name")
    world_root = portal_paths.world_root() / world
    fwl = saves_dir(world_root) / (world + ".fwl")
    database = saves_dir(world_root) / (world + ".db")
    container = f"valheim-server-{world}"

    seed_name = seed_value = None
    if fwl.is_file():
        seed_name, seed_value = read_seed(fwl)

    running = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                             capture_output=True, text=True).stdout.strip() == "true"
    logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
    text = logs.stdout + logs.stderr
    return {
        "world": world,
        "pinned_seed": pinned_seed(world_root),
        "fwl_present": fwl.is_file(),
        "seed_name": seed_name,
        "seed": seed_value,
        "seed_matches_pin": seed_name is not None and seed_name == pinned_seed(world_root),
        "db_bytes": database.stat().st_size if database.is_file() else 0,
        "locations_generated": "Done generating locations" in text,
        "world_loaded": f"Load world: {world}" in text,
        "server_running": running,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="create or reset a Valheim world on a chosen seed")
    parser.add_argument("world")
    parser.add_argument("seed", nargs="?", help="omit and pass --status to inspect without changing anything")
    parser.add_argument("--status", action="store_true", help="read-only report; performs no action")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(args.world), indent=1))
    else:
        if not args.seed:
            parser.error("seed is required unless --status is given")
        print(json.dumps(recreate(args.world, args.seed), separators=(",", ":")))
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
