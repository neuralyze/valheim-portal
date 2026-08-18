#!/usr/bin/env python3
"""Track the upstream projects this deployment builds from.

Mod versions already have a freshness check (`manage_mods.sh <world> check-updates`).
The projects we build *source* from had none, and it showed: on 2026-08-18 the VR mod's
checkout was a commit behind upstream, and the container project had changed owner months
earlier - `lloesche/valheim-server-docker` is now `community-valheim-tools/...` - with our
remote URL and our docs still naming the old one.

The rule this implements is NOT "always be on the newest commit". Forcing that would fail
unrelated work every time somebody else pushes. The rule is that upstream movement has to
be SEEN: the registry records the commit we last reviewed, and `status` fails while
upstream is ahead of that. Acknowledging it with `review` is a decision an operator makes
and records, with a note saying what they concluded - upgrade, skip, or wait.

    status            what each source is pinned to, reviewed to, and where upstream is
    review <id>       record that upstream movement was read, with a note
    verify            offline: the registry is well formed and matches the local checkouts

`verify` is the gate: it needs no network, so it runs in the ordinary check suite. `status`
reaches GitHub and belongs in a periodic run, not in a build.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

if __package__:
    from . import portal_paths
else:
    import portal_paths

REGISTRY = portal_paths.REPO_ROOT / "deploy" / "upstream-sources.json"
API = "https://api.github.com/repos/"
REQUIRED_FIELDS = ("id", "repo", "license", "why", "checkout_path",
                   "pinned_commit", "reviewed_commit", "reviewed_at")


class RegistryError(RuntimeError):
    """The registry is malformed, or disagrees with a checkout on disk."""


def load(path: Path | None = None) -> dict:
    path = path or REGISTRY
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except OSError as missing:
        raise RegistryError(f"cannot read {path}: {missing}") from missing
    except ValueError as invalid:
        raise RegistryError(f"{path} is not valid JSON: {invalid}") from invalid
    if registry.get("schema") != 1 or not isinstance(registry.get("sources"), list):
        raise RegistryError(f"{path} is not an upstream source registry")
    return registry


def save(registry: dict, path: Path | None = None) -> None:
    path = path or REGISTRY
    path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def source(registry: dict, identifier: str) -> dict:
    for entry in registry["sources"]:
        if entry.get("id") == identifier:
            return entry
    known = ", ".join(entry.get("id", "?") for entry in registry["sources"])
    raise RegistryError(f"no such source: {identifier} (known: {known})")


def checkout_head(entry: dict) -> str | None:
    """The commit the local checkout is on, or None when it is not present.

    Absent is not an error: a machine that only runs the portal has no VR mod checkout,
    and `verify` has nothing to compare there.
    """
    path = Path(entry["checkout_path"])
    if not (path / ".git").exists():
        return None
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def checkout_dirty(entry: dict) -> int | None:
    path = Path(entry["checkout_path"])
    if not (path / ".git").exists():
        return None
    result = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def verify(registry: dict) -> list[str]:
    """Offline checks. Returns the problems found, empty when the registry holds.

    Deliberately narrow: shape, uniqueness, and that a checkout on this machine is on the
    commit the registry claims. A pin that has silently drifted is the failure this
    catches - the artifacts we ship are built from that tree, so its commit is part of
    what we published.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for entry in registry["sources"]:
        identifier = entry.get("id", "<unnamed>")
        for field in REQUIRED_FIELDS:
            if not entry.get(field):
                problems.append(f"{identifier}: missing {field}")
        if identifier in seen:
            problems.append(f"{identifier}: duplicate id")
        seen.add(identifier)
        if entry.get("reviewed_commit") and entry.get("pinned_commit") and \
                not entry["reviewed_commit"].startswith(entry["pinned_commit"][:7]) and \
                not entry["pinned_commit"].startswith(entry["reviewed_commit"][:7]):
            # Reviewing a commit we are not on is legitimate - you can read what is coming
            # before taking it - but it has to be deliberate, so it must carry a note.
            if not entry.get("reviewed_note"):
                problems.append(f"{identifier}: reviewed_commit differs from pinned_commit without a note")
        head = checkout_head(entry)
        if head and not head.startswith(entry["pinned_commit"][:7]):
            problems.append(
                f"{identifier}: checkout is at {head[:12]}, registry pins {entry['pinned_commit'][:12]}")
        dirty = checkout_dirty(entry)
        if dirty and not entry.get("local_changes"):
            problems.append(f"{identifier}: checkout has {dirty} modified files and no local_changes note")
    return problems


def remote_head(repo: str, token: str | None = None) -> tuple[str, str, str]:
    """The newest commit on the default branch: (sha, date, subject)."""
    request = urllib.request.Request(f"{API}{repo}/commits?per_page=1",
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "valheim-portal-upstream-check"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, ValueError, TimeoutError) as failure:
        raise RegistryError(f"cannot read {repo} from GitHub: {failure}") from failure
    if not payload:
        raise RegistryError(f"{repo} returned no commits")
    commit = payload[0]
    return (commit["sha"], commit["commit"]["author"]["date"][:10],
            commit["commit"]["message"].splitlines()[0])


def status(registry: dict, token: str | None = None) -> tuple[list[dict], bool]:
    """One row per source, plus whether anything upstream is unreviewed."""
    rows, unreviewed = [], False
    for entry in registry["sources"]:
        row = {
            "id": entry["id"], "repo": entry["repo"],
            "pinned": entry["pinned_commit"][:12], "reviewed": entry["reviewed_commit"][:12],
            "reviewed_at": entry["reviewed_at"], "checkout": (checkout_head(entry) or "absent")[:12],
            "dirty": checkout_dirty(entry),
        }
        try:
            sha, when, subject = remote_head(entry["repo"], token)
            row.update(remote=sha[:12], remote_date=when, remote_subject=subject)
            row["reviewed_up_to_date"] = sha.startswith(entry["reviewed_commit"][:7])
        except RegistryError as failure:
            row.update(remote="unavailable", remote_subject=str(failure), reviewed_up_to_date=None)
        if row["reviewed_up_to_date"] is False:
            unreviewed = True
        rows.append(row)
    return rows, unreviewed


def review(registry: dict, identifier: str, commit: str, note: str) -> dict:
    entry = source(registry, identifier)
    if not note.strip():
        raise RegistryError("a review needs a note saying what you concluded")
    entry["reviewed_commit"] = commit
    entry["reviewed_at"] = date.today().isoformat()
    entry["reviewed_note"] = note.strip()
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--token", default=None, help="GitHub token, for rate limits")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="offline: the registry matches the checkouts")
    show = sub.add_parser("status", help="compare each source against its upstream")
    show.add_argument("--json", action="store_true")
    mark = sub.add_parser("review", help="record that upstream movement was read")
    mark.add_argument("id")
    mark.add_argument("--commit", required=True)
    mark.add_argument("--note", required=True)
    args = parser.parse_args(argv)

    registry = load(args.registry)
    if args.command == "verify":
        problems = verify(registry)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if not problems:
            print(f"upstream sources: {len(registry['sources'])} pinned and consistent")
        return 1 if problems else 0

    if args.command == "review":
        entry = review(registry, args.id, args.commit, args.note)
        save(registry, args.registry)
        print(f"reviewed {entry['id']} up to {entry['reviewed_commit'][:12]} on {entry['reviewed_at']}")
        return 0

    rows, unreviewed = status(registry, args.token)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            state = {True: "current", False: "UNREVIEWED", None: "unknown"}[row["reviewed_up_to_date"]]
            print(f'{row["id"]:22s} {state:11s} pinned={row["pinned"]} reviewed={row["reviewed"]} '
                  f'upstream={row["remote"]}')
            print(f'  {row["repo"]}  checkout={row["checkout"]}'
                  f'{f" dirty={row['dirty']}" if row["dirty"] else ""}')
            if row["reviewed_up_to_date"] is not True:
                print(f'  upstream head: {row.get("remote_date", "?")} {row.get("remote_subject", "")}')
    return 1 if unreviewed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RegistryError as failure:
        print(f"error: {failure}", file=sys.stderr)
        raise SystemExit(2)
