#!/usr/bin/env python3
"""Revision control for mod settings, so removing a mod cannot lose them.

Removing a mod deletes its config files. Until now the only copy was the
timestamped directory ``backup_removal_inputs`` writes beside the world, which
records the moment of one removal and nothing before it: there was no way to ask
"what did this setting say last week", and 65 configs for uninstalled mods sat in
the server tree precisely because deleting them felt unrecoverable.

This store answers both. It is a git repository holding **only the text an
operator owns** - the profile manifest, the client configs shipped in a profile,
and the server configs the plugins generate - mirrored out of the live trees on
every mutating mod operation. What it deliberately does not hold:

* ``manager-cache/`` - 2.1 GB of Thunderstore zips per profile, reproducible
  from the manifest, and the reason git-initialising a profile in place is not
  an option.
* ``config_merged/bepinex/plugins/`` - shipped DLLs and the cfgs inside a
  package, which belong to the mod, not to the operator.
* the ``*.before-*`` copies the publish scripts leave behind - history replaces
  that convention rather than versioning it.

The store lives beside the worlds (``<fleet root>/settings-history``) rather than
inside one of them, because the same settings are about to stop being per-world:
when a profile becomes a shared definition that several servers link to, the
layout below collapses from ``<world>/profiles/<profile>`` to ``<profile>`` and
nothing else about this module changes.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __package__:
    from . import profile_store
else:
    import profile_store

# git refuses to record a commit without an identity, and the host may have none
# configured for the account the portal agent runs as. Every other tool in this
# repository commits as the operator, so this matches rather than invents.
COMMIT_IDENTITY = (
    "-c", "user.name=operator",
    "-c", "user.email=operator@neuralyze.com",
)

# Suffixes an operator edits. `.json` is here for profile-manifest.json and
# `.yml` for the per-mod server lists (Azumatt.FastLink_servers.yml); a suffix
# not in this set is treated as payload, not settings.
SETTINGS_SUFFIXES = (".cfg", ".yml", ".yaml", ".json", ".txt")

# A config the publish scripts snapshotted (`Azumatt.WardIsLove.cfg.before-2026...`)
# or an editor left behind. Versioning these would record the same content twice.
BACKUP_MARKERS = (".before-", ".bak", "~")

STORE_ENVIRONMENT = "VALHEIM_SETTINGS_HISTORY"


class HistoryError(RuntimeError):
    """The store could not be read or written. Never raised for "no changes"."""


def store_path(fleet_root: Path) -> Path:
    """Where the repository lives for the fleet at ``fleet_root``.

    One store for the whole fleet: an operator asking what a setting used to say
    should not have to know which world happened to hold it.
    """
    override = os.environ.get(STORE_ENVIRONMENT, "")
    if override:
        if not override.startswith("/"):
            raise HistoryError(f"{STORE_ENVIRONMENT} must be an absolute path: {override}")
        return Path(override)
    return Path(fleet_root) / "settings-history"


def _git(store: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ("git", "-C", str(store), *arguments),
            capture_output=True, text=True,
        )
    except FileNotFoundError as missing:  # git absent from the host
        raise HistoryError("git is not installed; settings history cannot be recorded") from missing
    if check and result.returncode != 0:
        raise HistoryError(
            f"git {' '.join(arguments)} failed in {store}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result


def ensure_store(store: Path) -> Path:
    """Create the repository if it is absent. Safe to call on every operation.

    An unusable location - a file where the directory must go, a read-only mount -
    has to arrive as a HistoryError, because the caller's whole decision is
    "record first, then delete". A raw OSError here reached the operator as a
    traceback through the admin UI and skipped that decision entirely.
    """
    if (store / ".git").is_dir():
        return store
    try:
        store.mkdir(parents=True, exist_ok=True)
    except OSError as unusable:
        raise HistoryError(f"cannot create the settings store at {store}: {unusable}") from unusable
    _git(store, "init", "-q", "-b", "main")
    readme = store / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Valheim mod settings history\n\n"
            "Every commit here is one mod operation. The working tree is a mirror of the\n"
            "settings text in the live worlds, written by `tools/settings_history.py`;\n"
            "editing it by hand changes nothing that any server reads.\n\n"
            "Recover a setting a removal deleted:\n\n"
            "    python3 tools/settings_history.py show <path>\n"
            "    python3 tools/settings_history.py restore <path> --to /tmp/recovered.cfg\n\n"
            "`<path>` is a path inside this store, as printed by `log` or `list`.\n",
            encoding="utf-8",
        )
        _git(store, "add", "--", "README.md")
        _git(store, *COMMIT_IDENTITY, "commit", "-q", "-m", "Start the settings history")
    return store


def is_settings_file(path: Path) -> bool:
    if path.suffix.lower() not in SETTINGS_SUFFIXES:
        return False
    name = path.name
    return not any(marker in name for marker in BACKUP_MARKERS)


def tracked_files(fleet_root: Path) -> dict[str, Path]:
    """Map every settings file in the fleet to its path inside the store.

    Two kinds, and the split is the design: profile settings are shared, so they are
    keyed by profile with no world in the path; server settings are written by the
    plugins on each server's own first run, so they stay keyed by world.

    Ordering is by store path so a caller printing this reads the same list twice, and
    so the mirror walk below is deterministic.
    """
    fleet_root = Path(fleet_root)
    found: dict[str, Path] = {}

    profiles_root = profile_store.profiles_root(fleet_root)
    for name in profile_store.profile_names(profiles_root):
        profile = profiles_root / name
        found[f"profiles/{name}/profile-manifest.json"] = profile / profile_store.MANIFEST_NAME
        # client-config, client-config-flat, client-config-vr: the settings a player's
        # download carries. Named by prefix because a new client type is a directory,
        # not a code change.
        for directory in sorted(d for d in profile.iterdir()
                                if d.is_dir() and d.name.startswith("client-config")):
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.is_symlink() and is_settings_file(path):
                    relative = path.relative_to(profile).as_posix()
                    found[f"profiles/{name}/{relative}"] = path

    for world_dir in profile_store.worlds_in(fleet_root):
        server_config = world_dir / "config_merged" / "bepinex"
        if not server_config.is_dir():
            continue
        for path in sorted(server_config.rglob("*")):
            if not path.is_file() or path.is_symlink() or not is_settings_file(path):
                continue
            relative = path.relative_to(server_config)
            if relative.parts and relative.parts[0] == "plugins":
                continue  # a mod's own files, not the operator's settings
            found[f"{world_dir.name}/server-config/{relative.as_posix()}"] = path
    return found


def _mirror(store: Path, desired: dict[str, Path]) -> None:
    """Make the store equal ``desired``.

    Stale files are deleted rather than left: a removal has to appear in history as a
    deletion, otherwise the store answers "still there" forever. The whole tree is
    compared, not one world's subtree, because a profile is no longer inside a world -
    folding four copies into one has to read as four deletions and one addition.
    """
    keep = {"README.md"}
    for existing in sorted(store.rglob("*"), reverse=True):
        if ".git" in existing.relative_to(store).parts:
            continue
        if existing.is_file():
            relative = existing.relative_to(store).as_posix()
            if relative not in desired and relative not in keep:
                existing.unlink()
        elif existing.is_dir() and not any(existing.iterdir()):
            existing.rmdir()
    for relative, source in desired.items():
        destination = store / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = source.read_bytes()
            if not destination.exists() or destination.read_bytes() != payload:
                destination.write_bytes(payload)
        except OSError as unusable:
            # Either the live file vanished mid-run or the store is not writable.
            # Both are HistoryError so a caller about to delete settings can stop.
            raise HistoryError(f"cannot copy {source} into {destination}: {unusable}") from unusable


def snapshot(fleet_root: Path, message: str, store: Path | None = None,
             extra: dict[str, Path] | None = None) -> str | None:
    """Record the fleet's settings. Returns the commit, or None if unchanged.

    One commit covers every profile and every server, because one profile edit is one
    change to every server linked to it: splitting it per world would record the same
    edit four times and still not say they were the same edit.

    ``extra`` adds files this store would not otherwise see, keyed by their path inside
    it. The migration uses it to record the per-world profile copies before folding
    them, since after the fold those paths do not exist to be tracked. A later snapshot
    drops them from the working tree, which is the ordinary deletion case: gone from
    HEAD, still readable through `last_version`.

    "Unchanged" is the ordinary case - a `list` or a dry-run `update` changes nothing -
    so it is a return value and not an error.
    """
    fleet_root = Path(fleet_root).resolve()
    if not fleet_root.is_dir():
        raise HistoryError(f"not a directory: {fleet_root}")
    store = ensure_store(Path(store) if store else store_path(fleet_root))
    _mirror(store, {**tracked_files(fleet_root), **(extra or {})})
    _git(store, "add", "-A")
    if _git(store, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    _git(store, *COMMIT_IDENTITY, "commit", "-q", "-m", message)
    return _git(store, "rev-parse", "HEAD").stdout.strip()


def last_version(store: Path, relative: str) -> tuple[str, str]:
    """The newest content of ``relative``, whether or not it still exists.

    A deleted file's content is in the commit *before* the one that deleted it,
    which is the whole point of this store: `show` has to keep working after a
    removal, not stop working at exactly the moment it is needed.
    """
    store = ensure_store(Path(store))
    if _git(store, "cat-file", "-e", f"HEAD:{relative}", check=False).returncode == 0:
        return "HEAD", _git(store, "show", f"HEAD:{relative}").stdout
    deleted_in = _git(store, "rev-list", "-1", "HEAD", "--", relative).stdout.strip()
    if not deleted_in:
        raise HistoryError(f"no history for {relative}")
    reference = f"{deleted_in}~1"
    if _git(store, "cat-file", "-e", f"{reference}:{relative}", check=False).returncode != 0:
        raise HistoryError(f"no recorded content for {relative}")
    return reference, _git(store, "show", f"{reference}:{relative}").stdout


def history(store: Path, relative: str | None = None, limit: int = 20) -> list[str]:
    arguments = ["log", f"-{limit}", "--format=%h %ad %s", "--date=short"]
    if relative:
        arguments += ["--", relative]
    return [line for line in _git(ensure_store(Path(store)), *arguments).stdout.splitlines() if line]


def _fleet_from_argument(value: str) -> Path:
    path = Path(value)
    if not path.is_dir():
        raise SystemExit(f"error: not a directory: {value}")
    return path.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", help="override the store location")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot", help="record the fleet's settings now")
    snap.add_argument("fleet", help="path to the directory holding the worlds and profiles/")
    snap.add_argument("-m", "--message", default="manual snapshot")
    listing = sub.add_parser("list", help="print every settings file the store tracks")
    listing.add_argument("fleet")
    log = sub.add_parser("log", help="commits, newest first")
    log.add_argument("path", nargs="?", help="limit to one path inside the store")
    log.add_argument("-n", "--limit", type=int, default=20)
    show = sub.add_parser("show", help="print a file's newest recorded content")
    show.add_argument("path")
    restore = sub.add_parser("restore", help="write a recorded file somewhere")
    restore.add_argument("path")
    restore.add_argument("--to", required=True,
                         help="destination; never the live tree by default, so a "
                              "recovery cannot silently resurrect a mod's settings")
    args = parser.parse_args(argv)

    if args.command in ("snapshot", "list"):
        fleet = _fleet_from_argument(args.fleet)
        store = Path(args.store) if args.store else store_path(fleet)
        if args.command == "list":
            for relative in tracked_files(fleet):
                print(relative)
            return 0
        commit = snapshot(fleet, args.message, store)
        print(f"store={store}\n{'commit=' + commit if commit else 'unchanged'}")
        return 0

    if not args.store:
        raise SystemExit(f"error: --store or {STORE_ENVIRONMENT} is required for {args.command}")
    store = Path(args.store)
    if args.command == "log":
        for line in history(store, args.path, args.limit):
            print(line)
        return 0
    reference, content = last_version(store, args.path)
    if args.command == "show":
        print(f"# {args.path} at {reference}", file=sys.stderr)
        sys.stdout.write(content)
        return 0
    destination = Path(args.to)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    print(f"restored={destination} from={reference}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HistoryError as failure:
        print(f"error: {failure}", file=sys.stderr)
        raise SystemExit(2)
