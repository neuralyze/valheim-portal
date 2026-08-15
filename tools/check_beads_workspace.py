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


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root)
    for problem in problems:
        print(f"beads workspace: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("beads workspace: this project's tracker, no foreign remote")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
