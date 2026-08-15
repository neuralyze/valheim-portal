#!/usr/bin/env python3
"""Fail when policy.yaml and its documentation disagree.

The lane an agent works in is only as good as its weakest description. policy.yaml is what a
harness reads; docs/agent-harness.md is what a human reads. When those two drift, one of them
is a lie, and the prose is usually the one that rots - this repository spent a day proving that
standing rules in prose do not hold on their own.

Checked here: every verb has a class the policy defines, the documented table lists exactly the
same verbs with the same classes, mutating verbs are not silently approval-free, and the classes
that must require confirmation still do.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

MUTATING_CLASSES = {"world_state", "player_facing"}
README_COUNTS = re.compile(
    r"(?P<total>\d+) verbs declared\s*\n\s*(?P<execute>\d+) execute[^\n]*\n\s*(?P<refused>\d+) refused by design[^\n]*\n\s*(?P<forbidden>\d+) forbidden"
)
GO_VERB = re.compile(r'"(?P<verb>[a-z_]+)":\s*\{ID:\s*"(?P<id>[a-z_]+)",\s*Class:\s*Class(?P<cls>\w+)')
GO_CLASS_NAMES = {
    "Read": "read", "RepoWrite": "repo_write", "WorldState": "world_state",
    "PlayerFacing": "player_facing", "Forbidden": "forbidden",
}
REQUIRED_VERB_FIELDS = ("id", "class", "maps_to", "purpose")
TABLE_ROW = re.compile(r"^\|\s*`(?P<verb>[a-z_]+)`\s*\|\s*(?P<cls>[a-z_]+)\s*\|", re.MULTILINE)


def check(root: Path) -> list[str]:
    problems: list[str] = []
    policy_path = root / "policy.yaml"
    docs_path = root / "docs/agent-harness.md"
    try:
        policy = yaml.safe_load(policy_path.read_text())
    except FileNotFoundError:
        return [f"{policy_path} is missing"]
    except yaml.YAMLError as error:
        return [f"{policy_path} is not valid YAML: {error}"]

    classes = policy.get("classes") or {}
    verbs = policy.get("verbs") or []
    if not classes or not verbs:
        return [f"{policy_path} declares no classes or no verbs"]

    for name, body in classes.items():
        if "approval" not in (body or {}):
            problems.append(f"class {name!r} declares no approval")
    for name in MUTATING_CLASSES:
        approval = (classes.get(name) or {}).get("approval")
        if approval != "every_invocation":
            problems.append(
                f"class {name!r} has approval {approval!r}; mutating classes must be "
                "'every_invocation' - a deploy or a publish is never automatic"
            )

    seen: set[str] = set()
    for verb in verbs:
        for field in REQUIRED_VERB_FIELDS:
            if not verb.get(field):
                problems.append(f"verb {verb.get('id', '<unnamed>')!r} is missing {field}")
        verb_id, verb_class = verb.get("id"), verb.get("class")
        if verb_id in seen:
            problems.append(f"verb {verb_id!r} is declared twice")
        seen.add(verb_id)
        if verb_class not in classes:
            problems.append(f"verb {verb_id!r} has class {verb_class!r}, which policy.yaml does not define")
        if verb_class == "player_facing" and not verb.get("evidence") and not verb.get("requires"):
            problems.append(
                f"verb {verb_id!r} is player-facing but states no evidence requirement; a claim "
                "about a release must be read back out of the artifact"
            )

    if not docs_path.is_file():
        problems.append(f"{docs_path} is missing")
        return problems

    # Only the verb table counts. The document also has an approval-class table whose rows
    # look identical in shape, and reading those as verbs is a false failure.
    text = docs_path.read_text()
    start = text.find("## Verbs")
    section = text[start:] if start >= 0 else text
    end = section.find("\n## ", 1)
    if end > 0:
        section = section[:end]
    documented = {m.group("verb"): m.group("cls") for m in TABLE_ROW.finditer(section)}
    declared = {verb["id"]: verb["class"] for verb in verbs if verb.get("id")}
    for verb_id, verb_class in declared.items():
        if verb_id not in documented:
            problems.append(f"verb {verb_id!r} is in policy.yaml but not in the documented table")
        elif documented[verb_id] != verb_class:
            problems.append(
                f"verb {verb_id!r} is {verb_class!r} in policy.yaml and "
                f"{documented[verb_id]!r} in the documentation"
            )
    for verb_id in documented:
        if verb_id not in declared:
            problems.append(f"verb {verb_id!r} is documented but absent from policy.yaml")

    # The Go table is what the portal enforces. A verb that exists in the policy but not in the
    # code is unenforced; one that exists only in the code is undeclared. Both are drift.
    registry = root / "internal/app/verbs.go"
    if registry.is_file():
        registry_text = registry.read_text()
        implemented: dict[str, str] = {}
        for match in GO_VERB.finditer(registry_text):
            if match.group("verb") != match.group("id"):
                problems.append(
                    f"internal/app/verbs.go: map key {match.group('verb')!r} does not match "
                    f"its ID field {match.group('id')!r}"
                )
            implemented[match.group("id")] = GO_CLASS_NAMES.get(match.group("cls"), match.group("cls"))
        for verb_id, verb_class in declared.items():
            if verb_id not in implemented:
                problems.append(f"verb {verb_id!r} is declared in policy.yaml but absent from internal/app/verbs.go")
            elif implemented[verb_id] != verb_class:
                problems.append(
                    f"verb {verb_id!r} is {verb_class!r} in policy.yaml and "
                    f"{implemented[verb_id]!r} in internal/app/verbs.go"
                )
        for verb_id in implemented:
            if verb_id not in declared:
                problems.append(f"verb {verb_id!r} is implemented in internal/app/verbs.go but not declared in policy.yaml")

        # The README states how many verbs execute today. A number in prose rots the moment a
        # verb is wired, and a stale number in an installation guide is worse than none: it is
        # the sort of claim someone acts on. So it is checked against the code that answers it.
        readme = root / "README.md"
        if readme.is_file():
            text = readme.read_text()
            match = README_COUNTS.search(text)
            if match is None:
                if "verb-counts:" in text:
                    problems.append("README.md carries the verb-counts marker but the block no longer parses")
            else:
                executes = sum(1 for verb in verbs if verb.get("class") != "forbidden" and _wired(registry_text, verb["id"]))
                forbidden = sum(1 for verb in verbs if verb.get("class") == "forbidden")
                refused = len(verbs) - executes - forbidden
                for name, stated, actual in (
                    ("total", int(match.group("total")), len(verbs)),
                    ("executing", int(match.group("execute")), executes),
                    ("refused by design", int(match.group("refused")), refused),
                    ("forbidden", int(match.group("forbidden")), forbidden),
                ):
                    if stated != actual:
                        problems.append(
                            f"README.md says {stated} {name} verb(s); policy.yaml and "
                            f"internal/app/verbs.go say {actual}"
                        )
    return problems


def _wired(registry_text: str, verb_id: str) -> bool:
    """Whether the Go table gives this verb an Operation, i.e. the portal can actually run it."""
    match = re.search(
        r'"' + re.escape(verb_id) + r'":\s*\{ID:\s*"' + re.escape(verb_id) + r'",[^\n]*',
        registry_text,
    )
    return bool(match) and "Operation:" in match.group(0)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root)
    for problem in problems:
        print(f"agent policy: {problem}", file=sys.stderr)
    if problems:
        return 1
    policy = yaml.safe_load((root / "policy.yaml").read_text())
    counts: dict[str, int] = {}
    for verb in policy["verbs"]:
        counts[verb["class"]] = counts.get(verb["class"], 0) + 1
    summary = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    print(f"agent policy: {len(policy['verbs'])} verbs consistent with the docs ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
