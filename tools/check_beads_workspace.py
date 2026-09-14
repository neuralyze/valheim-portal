#!/usr/bin/env python3
"""Fail when the beads workspace in this checkout is not this project's.

On 4 Aug a `bd init` run inside this repository cloned an unrelated project's tracker into
`.beads/`: 856 issues belonging to a Godot game, sitting in the Valheim repository, diverging
from their own origin. It happened because `bd init` inherits `sync.remote` from the nearest
workspace above it, and this checkout lives inside another project's tree. Reproduced on
14 Aug while fixing it: a fresh init pulled 848 of those issues straight back in.

`.beads/expected-project.json` is committed and names the project this repository owns. The
local database is not committed, so this check is what notices when the two disagree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise SystemExit(f"{path}: unreadable: {error}")


def check(root: Path) -> list[str]:
    problems: list[str] = []
    expected = _load(root / ".beads/expected-project.json")
    if not expected.get("project_id"):
        return [".beads/expected-project.json is missing or has no project_id"]

    metadata_path = root / ".beads/metadata.json"
    metadata = _load(metadata_path)
    if not metadata:
        # A fresh clone has no local database yet, which is the normal state in CI.
        return problems

    actual = metadata.get("project_id")
    if actual != expected["project_id"]:
        problems.append(
            f"{metadata_path} belongs to project {actual}, "
            f"but this repository owns {expected['project_id']}. "
            "A bd init here inherits the parent workspace's sync.remote and clones its tracker; "
            "re-create with: rm -rf .beads && bd init --prefix "
            f"{expected.get('prefix', 'vhp')} --remote ''"
        )

    config = (root / ".beads/config.yaml")
    if config.is_file():
        for line in config.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "sync.remote" not in stripped:
                continue
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            if value and value != expected.get("sync_remote", ""):
                problems.append(
                    f"{config}: sync.remote is {value!r}, which this repository does not own. "
                    "An inherited remote re-clones another project's issues on the next sync."
                )
    return problems


def check_export_is_current(root: Path) -> list[str]:
    """Fail when .beads/issues.jsonl is behind the local database.

    The Dolt database under .beads/embeddeddolt/ is gitignored, so issues.jsonl is the
    ONLY copy of the tracker that leaves this machine. `bd close` and `bd note` write
    the database and do not rewrite that file, and the export that does rewrite it is
    driven by a commit hook - so any bead work done after the last commit of the day
    exists on this host and nowhere else.

    Measured on 2026-09-14: five beads were closed and three annotated after the final
    commit. `git status` was clean, `bd show` reported every one of them CLOSED, and the
    committed issues.jsonl still said `open` for all five. Nothing in the repository
    disagreed with itself, so nothing complained. The fix is one `bd export -o` away and
    the whole cost of missing it is silent: the next clone simply believes the old state.
    """
    problems: list[str] = []
    tracked = root / ".beads/issues.jsonl"
    if not tracked.is_file() or not (root / ".beads/metadata.json").is_file():
        # No local database, or no export yet: a fresh clone, which is normal in CI.
        return problems

    binary = shutil.which("bd")
    if binary is None:
        # Say so rather than passing quietly - a silent skip here is the same class of
        # failure this check exists to catch.
        print("beads workspace: bd is not on PATH, so export freshness was NOT checked", file=sys.stderr)
        return problems

    try:
        result = subprocess.run(
            [binary, "export"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f"could not run `bd export` to check {tracked} is current: {error}"]
    if result.returncode != 0:
        return [f"`bd export` failed, so {tracked} could not be checked: {result.stderr.strip()[:200]}"]

    # Compare the SET of records rather than the bytes. The export's line order is not
    # guaranteed stable between runs, and a reordering is not drift.
    def records(text: str) -> set[str]:
        out = set()
        for line in text.splitlines():
            line = line.strip()
            if line:
                out.add(line)
        return out

    live = records(result.stdout)
    committed = records(tracked.read_text())
    if live != committed:
        only_live = len(live - committed)
        only_committed = len(committed - live)
        problems.append(
            f"{tracked} is not what the local database holds "
            f"({only_live} record(s) newer in the database, {only_committed} stale in the file). "
            "Bead state lives in a gitignored Dolt database, so this file is the only copy "
            "that leaves the machine. Refresh and commit it: bd export -o .beads/issues.jsonl"
        )
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root) + check_export_is_current(root)
    for problem in problems:
        print(f"beads workspace: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("beads workspace: this project's tracker, no foreign remote, export is current")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
