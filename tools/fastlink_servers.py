#!/usr/bin/env python3
"""Generate each world's FastLink server list from portal data.

FastLink (Azumatt-FastLink) reads `Azumatt.FastLink_servers.yml` and draws one row per
top-level key in the main menu. The file is a YAML mapping of DISPLAY NAME to an entry:

    Ulfsland:
      address: valheim.neuralyze.com
      port: 2469
      password: DeepNorthTest26

The mod deserialises it as Dictionary<string, FastLink.Definition>; Definition's
properties are `address`, `port` (uint16), `password`, `ispvp`, `iscrossplay` and
`iswhitelisted`, all optional except the two that name a server. Any entry count from 1
up is valid: FastLink.Util.UpdateServerList branches on `entries.Count > 0` only, and
logs "No servers defined" solely for an empty or missing file.

This file was hand-maintained and was wrong twice. Once it held a single stale entry, so
three worlds' players were pointed at a fourth world's port. Then it held all five
worlds in every edition, so each world's client advertised its neighbours. Both were
invisible until someone read the published zip. So it is generated, from the three places
that actually know:

  address   the host half of `join_address` in the portal database
  port      MEASURED: `docker port valheim-server-<World>` for a running world, else the
            host half of CONTAINER_VALHEIM_PORT in that world's valheim.env
  password  SERVER_PASS in that world's valheim.env, omitted when unset

A world is its own list. The file is written as a WHOLE-FILE per-world override at
<VALHEIM_ROOT>/<World>/mods/overrides/client/Azumatt.FastLink_servers.yml, which
republish-profiles.sh layers over the shared profile's client-config at publish time.
Whole-file is correct here and only here: tools/config_merge.py merges .cfg per key and
lets any other suffix win whole, and the entire point of this file is that each world's
copy differs completely. A per-key merge would union the lists again.

The measured port and the port the portal advertises must agree. When they do not, the
world is refused rather than written with a guess: a disagreement means one of the two is
stale, and shipping either one silently is how this file went wrong the first time.

DO NOT DELETE the shared profiles' client-config/Azumatt.FastLink_servers.yml. Now that
every world overrides it whole it looks redundant, and it is not. Measured against the
mod's own code: FastLink.Patches.Servers::Init does `if (!File.Exists(ConfigPath))` and
writes its own template - "Example Server" at example.com:1234, 93.184.216.34:9999 and
friends - so an edition shipping no file at all puts FAKE SERVERS in front of every
player. A file with no entries is no better: Parse throws ArgumentNullException on it.

So that file stays as the fallback a world with no override of its own would ship. Such
a world advertises its neighbours, which is wrong, but every row it shows resolves.
`check` is what keeps that from happening quietly: it names any enabled world whose
override is missing or stale and exits 1.

usage:
  fastlink_servers.py write [--world NAME]...     write each world's override
  fastlink_servers.py check [--world NAME]...     report drift, exit 1 if any
  fastlink_servers.py show  --world NAME          print one world's list

Environment:
  VALHEIM_ROOT      required  directory holding one subdirectory per world
  PORTAL_DATABASE   optional  defaults to /var/lib/valheim-portal/portal.sqlite
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import portal_paths
from valheim_provision import CONTAINER_GAME_PORT, parse_env

DEFAULT_DATABASE = Path("/var/lib/valheim-portal/portal.sqlite")
CONFIG_NAME = "Azumatt.FastLink_servers.yml"
# YAML plain scalars cannot start an indicator or hold `: ` / ` #`, and a value that looks
# like a boolean or a number would still reach a string property as text - but through a
# resolver, which is a behaviour to depend on rather than sidestep. Quote when the plain
# form is not unambiguous, and only then, so a password stays readable in the file.
NEEDS_QUOTING = set("#,[]{}&*!|>'\"%@`:?-")


class GeneratorError(RuntimeError):
    """A world cannot be described: no address, no port, or two disagreeing ports."""


@dataclass(frozen=True)
class Server:
    """One world's joinable identity, with where each half was measured."""
    world: str
    address: str
    port: int
    password: str
    port_source: str


def scalar(value: str) -> str:
    """``value`` as a YAML scalar, quoted only when the plain form is ambiguous."""
    if not value or value != value.strip() or value[0] in NEEDS_QUOTING or ": " in value or " #" in value:
        return "'" + value.replace("'", "''") + "'"
    return value


def published_port(env: dict[str, str]) -> int | None:
    """The host half of CONTAINER_VALHEIM_PORT, which is the port a client joins.

    SERVER_PORT is the container-side bind - 2456 for every world - so reading it instead
    hands every world the same port. Measured on Ulfsland: SERVER_PORT='2456',
    CONTAINER_VALHEIM_PORT='2469-2470', and `docker port` maps 2456/udp to host 2469.
    """
    first = env.get("CONTAINER_VALHEIM_PORT", "").split("-", 1)[0]
    return int(first) if first.isdigit() else None


def mapped_port(world: str) -> int | None:
    """The host port docker actually publishes for the world's game port, or None.

    None means "not running", not "no port": a stopped world has no mapping to read and
    falls back to its env file. Requires docker on PATH; absent, every world falls back.
    """
    if not shutil.which("docker"):
        return None
    try:
        result = subprocess.run(
            ["docker", "port", f"valheim-server-{world}", f"{CONTAINER_GAME_PORT}/udp"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        _, _, host = line.strip().rpartition(":")
        if host.isdigit():
            return int(host)
    return None


def join_addresses(database: Path) -> dict[str, str]:
    """Every enabled world's advertised ``host:port``, keyed by world name."""
    if not database.is_file():
        raise GeneratorError(f"no portal database: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select name, join_address from public_worlds where enabled=1").fetchall()
    finally:
        connection.close()
    return {name: address for name, address in rows if address}


def describe(world: str, advertised: str, env_path: Path) -> Server:
    """One world's Server, refusing rather than guessing when the two ports disagree."""
    host, _, advertised_port = advertised.rpartition(":")
    if not host or not advertised_port.isdigit():
        raise GeneratorError(f"{world}: portal join_address is not host:port: {advertised!r}")

    env = parse_env(env_path)
    measured = mapped_port(world)
    declared = published_port(env)
    if measured is not None:
        port, source = measured, f"docker port valheim-server-{world}"
    elif declared is not None:
        port, source = declared, f"CONTAINER_VALHEIM_PORT in {env_path}"
    else:
        raise GeneratorError(
            f"{world}: no port to measure - not running and no CONTAINER_VALHEIM_PORT "
            f"in {env_path}")

    if port != int(advertised_port):
        raise GeneratorError(
            f"{world}: port {port} from {source} disagrees with the portal's "
            f"{advertised} - one of the two is stale, fix it before publishing")
    if measured is not None and declared is not None and measured != declared:
        raise GeneratorError(
            f"{world}: docker publishes {measured} but CONTAINER_VALHEIM_PORT says "
            f"{declared} in {env_path}")

    return Server(world, host, port, env.get("SERVER_PASS", ""), source)


def render(server: Server) -> str:
    """``server`` as the whole file FastLink reads: a header, then one entry.

    The header names no measured path, only the generator. Where the port came from is
    reported to the operator instead: a running world measures it from docker and a
    stopped one from its env file, so writing that into the file would make it flap
    between two byte-identical-in-meaning versions and turn `check` into noise.
    """
    lines = [
        "# Generated by tools/fastlink_servers.py - do not edit by hand.",
        "# One entry: the world this client edition was published for. Address from the",
        "# portal database, port measured per world, password from that world's valheim.env.",
        f"{scalar(server.world)}:",
        f"  address: {scalar(server.address)}",
        f"  port: {server.port}",
    ]
    if server.password:
        lines.append(f"  password: {scalar(server.password)}")
    return "\n".join(lines) + "\n"


def override_path(root: Path, world: str) -> Path:
    return root / world / "mods" / "overrides" / "client" / CONFIG_NAME


def servers(root: Path, database: Path, wanted: list[str]) -> list[Server]:
    """Every requested world's Server, or every enabled world that exists on disk."""
    advertised = join_addresses(database)
    if wanted:
        missing = [world for world in wanted if world not in advertised]
        if missing:
            raise GeneratorError(
                f"not enabled public worlds in {database}: {', '.join(missing)}")
        names = wanted
    else:
        names = sorted(world for world in advertised if (root / world).is_dir())
    return [describe(world, advertised[world], root / world / "valheim.env") for world in names]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("write", "check", "show"))
    parser.add_argument("--world", action="append", default=[],
                        help="world to generate; repeatable, defaults to every enabled world")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args(argv)

    try:
        root = portal_paths.world_root()
    except portal_paths.ConfigurationError as error:
        print(error, file=sys.stderr)
        return portal_paths.EX_CONFIG

    try:
        wanted = servers(root, args.database, args.world)
    except GeneratorError as error:
        print(error, file=sys.stderr)
        return 1
    if not wanted:
        print("no worlds to generate", file=sys.stderr)
        return 1

    if args.command == "show":
        for server in wanted:
            sys.stdout.write(render(server))
        return 0

    drift = 0
    for server in wanted:
        path = override_path(root, server.world)
        wants = render(server)
        has = path.read_text(errors="replace") if path.is_file() else ""
        if has == wants:
            print(f"{server.world}: up to date ({server.address}:{server.port})")
            continue
        drift += 1
        if args.command == "check":
            print(f"{server.world}: {'stale' if has else 'missing'} {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wants)
        print(f"{server.world}: wrote {path} ({server.address}:{server.port}, "
              f"port from {server.port_source})")
    return 1 if args.command == "check" and drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
