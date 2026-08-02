"""Path resolution shared by the portal's host-side Python tools.

These tools ship in the same repository as hostops/ and the Go portal, so
anything inside the repository is resolved from ``__file__`` and a clone works
wherever it is checked out. Anything outside the repository -- the Valheim world
root and the valheim-server-docker checkout -- is deliberately configuration
with no default, on exactly the terms hostops/lib/common.sh already applies to
the shell scripts: an unset variable is a loud EX_CONFIG failure naming the
variable, never a guess.

The resolvers are functions rather than import-time constants on purpose. A tool
that only prints ``--help``, and the unit tests that import valheim_mods for its
pure functions, must not be forced to configure a world root they never touch.
"""
from __future__ import annotations

import os
from pathlib import Path

# sysexits.h EX_CONFIG. hostops/lib/common.sh exits 78 for the same conditions,
# so the portal agent and an operator see one exit code whichever half failed.
EX_CONFIG = 78

TOOLS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_ROOT.parent
HOSTOPS_ROOT = REPO_ROOT / "hostops"


class ConfigurationError(RuntimeError):
    """A required deployment path is unset, relative, or absent.

    Distinct from RuntimeError so each tool's entry point can exit EX_CONFIG for
    a misconfigured host and keep its ordinary failure code for a real error.
    """


def _resolve(variable: str, value: str) -> Path:
    if not value.startswith("/"):
        raise ConfigurationError(f"{variable} must be an absolute path: {value}")
    path = Path(value)
    if not path.is_dir():
        raise ConfigurationError(f"{variable} is not a directory: {value}")
    return path.resolve()


def world_root() -> Path:
    """The directory holding one subdirectory per world, plus world_backups.

    Accepts the same three names hostops/lib/common.sh accepts, so a tool
    invoked by a host script, by the portal agent's systemd unit, or by hand
    reads one configured value.
    """
    for variable in ("VALHEIM_ROOT", "AGENT_WORLD_ROOT", "VALHEIM_WORLD_ROOT"):
        value = os.environ.get(variable, "")
        if value:
            return _resolve(variable, value)
    raise ConfigurationError(
        "VALHEIM_ROOT is not set. Set it to the directory holding one "
        "subdirectory per world plus world_backups, for example "
        "VALHEIM_ROOT=/srv/valheim. The portal installer calls the same "
        "directory VALHEIM_WORLD_ROOT and the portal agent exports it as "
        "AGENT_WORLD_ROOT; either is accepted too. There is no default: "
        "guessing a path would mean deleting or overwriting the wrong one."
    )


def server_docker_dir() -> Path:
    """The checkout of the modified valheim-server-docker fork.

    That tree is a separate Apache-2.0 project and is not vendored here, so it
    is configuration. Provisioning reads its default.env for the PGID the
    container chowns its mounts to; getting that wrong leaves a world the
    container cannot write.
    """
    value = os.environ.get("VALHEIM_SERVER_DOCKER_DIR", "")
    if not value:
        raise ConfigurationError(
            "VALHEIM_SERVER_DOCKER_DIR is not set. Set it to a checkout of the "
            "modified valheim-server-docker fork -- the directory holding "
            "docker-compose.yaml and default.env -- for example "
            "VALHEIM_SERVER_DOCKER_DIR=/srv/valheim-server-docker. There is no "
            "default: the compose project there is what the lifecycle scripts "
            "start, stop and remove."
        )
    return _resolve("VALHEIM_SERVER_DOCKER_DIR", value)
