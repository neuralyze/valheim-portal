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
import dataclasses
import json
import os
import re
import struct
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


# Valheim 1.0.12 (network version 40) replaced the worlds_local/<World>.db +
# worlds_local/<World>.fwl pair with a DIRECTORY worlds_local/<World>/ holding one
# generation of files per save. Measured on the live Ulfsland 2026-09-14:
#
#   Ulfsland/_main.2.fwl2      171 bytes   world metadata
#   Ulfsland/_main.2.db2    168100        the world database
#   Ulfsland/_main.2.chunks     21
#   Ulfsland/_main.2.ok          4        int32 41, the world version
#   Ulfsland/00_00__0_2.chunk 1464        one file per saved zone
#
# So neither of the two paths status() used to build existed, and every field derived
# from them read empty for a world that was loaded and running: the one tool whose job
# is to confirm a re-roll took effect could not see the world it had just created.
#
# The layout is DETECTED, never assumed, and both tests are positive - the same
# discipline hostops/lib/common.sh resolve_world_save applies, for the same reason.
# "Not a pair" must not imply 1.0, and "is a directory" must not imply 1.0 either:
# worlds_local also holds the game's own rolling backups, which under 1.0 are
# directories (Ulfsland_backup_auto-20260914-043557/, measured) and under 0.220.x are
# <World>_backup_auto-*.db files. A backup directory is rejected because it is not
# named for the world, and a directory named for the world but holding no *.fwl2 -
# the half-created case that once hung the 1.0 server in the start scene - is rejected
# too. Both casings are accepted in the stem, because the server writes the save under
# whichever casing WORLD_NAME carried and the worlds created before the portal existed
# are lowercase.
GENERATION_RE = re.compile(r"\.(\d+)\.fwl2\Z")

# Valheim 1.0.12's log vocabulary is not 0.220's. Measured against the live server and
# against assembly_valheim.dll's own format strings: neither "Load world: <World>" nor
# "Done generating locations" appears ANYWHERE in a running 1.0 server's log, so the two
# log-derived fields also read false for a world that is loaded and generated. 1.0 says
# "ZNet.LoadWorld: <World> (<stem>), save number <N>" instead, and reports locations
# either as "ZoneSystem.Load => Loaded 14,040 locations" (read back off an existing
# save) or, on the generation pass itself, "Loading: Done. ... Genloc duration: <n> ms".
# The 0.220 strings are kept: four worlds on this host are still on that build.
LOCATION_MARKERS = ("Done generating locations", "ZoneSystem.Load => Loaded ", "Genloc duration:")


def load_markers(world: str) -> tuple[str, ...]:
    return (f"Load world: {world}", f"ZNet.LoadWorld: {world} (")


@dataclasses.dataclass(frozen=True)
class WorldSave:
    """One world's save on disk, in whichever of the two layouts it actually uses.

    ``header`` is the parsed metadata of the newest generation that PARSES, or None when
    the save has no readable metadata. Parsing during resolution, rather than leaving it
    to the caller, is what lets a read-only report say "the save is there, its metadata
    is not readable" instead of raising: the highest-numbered .fwl2 is the one the game
    may be writing right now.
    """

    layout: str          # "directory" (Valheim 1.0) or "pair" (0.220.x)
    stem: str            # the name the save carries on disk, which is not always the world's casing
    path: Path           # the world directory (1.0) or the .fwl (pair)
    metadata: Path | None
    header: dict | None
    total_bytes: int


def generation(path: Path) -> tuple[int, str]:
    match = GENERATION_RE.search(path.name)
    return (int(match.group(1)) if match else -1, path.name)


def readable_metadata(candidates: list[Path]) -> tuple[Path, dict] | None:
    """The newest candidate whose header parses, with that header. Newest first.

    Highest generation wins, numerically, so _main.10.fwl2 beats _main.9.fwl2 -
    hostops/portal_world_metadata.sh version-sorts for the same reason. Which generation
    is read barely matters for the seed: Ulfsland's _main.0.fwl2 and _main.1.fwl2 carry
    the same name, seed, seed value, uid and generator version and differ only in the
    trailer. Skipping a truncated newest generation therefore costs nothing, while
    trusting it would report a world that has a seed as having none.
    """
    for candidate in sorted(candidates, key=generation, reverse=True):
        try:
            return candidate, valheim_world.parse(candidate)
        except (OSError, UnicodeDecodeError, ValueError, struct.error):
            continue
    return None


def directory_bytes(directory: Path) -> int:
    """Every file of a 1.0 save summed, which is the analogue of the pair's .db size."""
    total = 0
    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_file():
            continue
        total += entry.stat().st_size
    return total


def resolve_world_save(world_root: Path, world: str) -> WorldSave | None:
    """The world's save on disk, or None when it has none. Read-only.

    A world matching neither layout is reported as None to the caller rather than left
    to surface as an empty seed on a world that is plainly running.
    """
    saves = saves_dir(world_root)
    stems = list(dict.fromkeys((world, world.lower())))
    for stem in stems:
        directory = saves / stem
        if directory.is_symlink() or not directory.is_dir():
            continue
        headers = sorted(directory.glob("*.fwl2"))
        if headers:
            metadata, header = readable_metadata(headers) or (None, None)
            return WorldSave("directory", stem, directory, metadata, header,
                             directory_bytes(directory))
    for stem in stems:
        fwl, database = saves / (stem + ".fwl"), saves / (stem + ".db")
        if fwl.is_file() and database.is_file():
            metadata, header = readable_metadata([fwl]) or (None, None)
            return WorldSave("pair", stem, fwl, metadata, header, database.stat().st_size)
    return None


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
    """Move every save file for this world aside. Never deletes; returns the directory.

    Both casings, because resolve_world_save accepts either: a save left behind here is
    the one the server loads next, which would make the re-roll a silent no-op.
    """
    source = saves_dir(world_root)
    stems = list(dict.fromkeys((world_root.name, world_root.name.lower())))
    files = sorted({path for stem in stems for path in source.glob(stem + "*")}) if source.is_dir() else []
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


def require_script(name: str) -> Path:
    script = HOSTOPS_ROOT / name
    if not script.is_file():
        raise RuntimeError(f"missing host script: {script}")
    return script


def run_script(name: str, world: str) -> None:
    result = subprocess.run([str(require_script(name)), world], capture_output=True, text=True)
    # Both lifecycle scripts chat on stdout; stdout here belongs to the JSON result.
    sys.stderr.write(result.stdout + result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"{name} failed for {world} with status {result.returncode}")


def preflight(world: str, seed: str) -> Path:
    """Prove every prerequisite BEFORE recreate() stops the server or moves a save.

    On 2026-09-14 the missing-plugin refusal fired AFTER the stop and AFTER the archive:
    the run left the world down with an empty worlds_local and its only copy inside
    world-archive-20260914T085408-worldgen. Nothing here touches the host, so a request
    this tool cannot satisfy now costs the operator nothing at all - which is the whole
    point of checking the start script too, not just the stop: discovering that one
    missing after the stop strands the world down.
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
    if not PLUGIN_SOURCE.is_file():
        raise RuntimeError(
            f"world seed plugin is missing: {PLUGIN_SOURCE}; build it with "
            f"{TOOLS_ROOT / 'worldseed' / 'build.sh'} {world}, which compiles against "
            "that world's own on-disk assemblies and needs no running container")
    for name in ("stop_valheim_server.sh", "start_valheim_server.sh"):
        require_script(name)
    # The archive directory and the plugin both land under config_merged.
    writable = world_root / "config_merged"
    if not writable.is_dir():
        writable = world_root
    if not os.access(writable, os.W_OK | os.X_OK):
        raise RuntimeError(f"cannot write {writable}; run as the world's owner or root")
    return world_root


def recreate(world: str, seed: str) -> dict:
    """Check everything, then stop, archive, pin the seed, start. Perform the operation;
    do not judge the result.

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

    What IS checked is every precondition, and all of it before the first disruptive
    step: see preflight. A refusal must not be able to leave a world stopped.
    """
    world_root = preflight(world, seed)
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
    save = resolve_world_save(world_root, world)
    container = f"valheim-server-{world}"
    pin = pinned_seed(world_root)

    seed_name = seed_value = world_version = None
    if save is not None and save.header is not None:
        seed_name, seed_value, world_version = (
            save.header["seed"], save.header["seed_value"], save.header["world_version"])

    running = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                             capture_output=True, text=True).stdout.strip() == "true"
    logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
    text = logs.stdout + logs.stderr
    return {
        "world": world,
        "save_format": save.layout if save else None,
        "save_path": str(save.path) if save else None,
        "pinned_seed": pin,
        "fwl_present": save is not None and save.metadata is not None,
        # Which generation the seed was read from: under 1.0 a save holds several, and
        # naming the file is what makes the report checkable by hand against `strings`.
        "metadata_file": str(save.metadata) if save and save.metadata else None,
        "seed_name": seed_name,
        "seed": seed_value,
        "world_version": world_version,
        "seed_matches_pin": seed_name is not None and seed_name == pin,
        "db_bytes": save.total_bytes if save else 0,
        "locations_generated": any(marker in text for marker in LOCATION_MARKERS),
        "world_loaded": any(marker in text for marker in load_markers(world)),
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
