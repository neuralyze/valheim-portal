#!/usr/bin/env python3
"""Fail C# that does unbounded work on a per-frame path.

On 13 Aug a label search shipped inside Update(). It scanned every Text and TextMeshPro object
in memory once a second, allocating two strings per label, and stopped only after hiding
something - which in a loaded world never happens, because the panel it looked for belongs to
the start screen. It ran for entire play sessions and collapsed the frame rate for a day. The
defect was visible in the source the whole time.

This is a text-level check, not a compiler. It finds methods reachable from Unity's per-frame
entry points and refuses two things there: scene-wide object searches, and allocation-heavy
constructs. A search that genuinely belongs on a frame path must say what bounds it:

    // lint:per-frame bounded by MaxLooks and the InWorld guard

The comment can sit on the offending line or anywhere in the enclosing method. Requiring the
sentence is the point: it makes the author name the bound, and a reviewer can check the claim.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PER_FRAME_ENTRIES = {
    "Update", "LateUpdate", "FixedUpdate", "OnGUI",
    "OnPreRender", "OnPostRender", "OnRenderObject", "OnPreCull",
}

SCENE_SEARCHES = re.compile(
    r"\b("
    r"Resources\.FindObjectsOfTypeAll|FindObjectsOfTypeAll|FindObjectsOfType|FindObjectsByType"
    r"|GameObject\.Find|FindGameObjectsWithTag|FindGameObjectWithTag"
    r"|GetComponentsInChildren|GetComponentsInParent"
    r"|TypeByName|GetTypes\(\)|GetAssemblies\(\)"
    r")\b"
)

# Only applied to the per-frame method's own body, where the cost is unconditional.
ALLOCATIONS = re.compile(
    r"(\bnew\s+(?:List|Dictionary|HashSet|Queue|Stack|StringBuilder)\b"
    r"|\bnew\s+\w+\[\]"
    r"|\.ToList\(\)|\.ToArray\(\)|\.Select\(|\.Where\(|\.OrderBy\("
    r"|string\.Format\(|\$\")"
)

ANNOTATION = re.compile(r"lint:per-frame\b")
METHOD = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*(?:public|private|internal|protected|static|virtual|override|sealed|extern|unsafe|async|\s)*"
    r"(?:[\w<>\[\],\.\?]+\s+)+(?P<name>\w+)\s*\((?P<args>[^;{)]*)\)\s*(?:where[^{]*)?\{",
    re.MULTILINE,
)


class Method:
    __slots__ = ("name", "path", "line", "body")

    def __init__(self, name: str, path: Path, line: int, body: str) -> None:
        self.name, self.path, self.line, self.body = name, path, line, body


def _body_from(text: str, open_brace: int) -> str:
    depth, index = 0, open_brace
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : index + 1]
        index += 1
    return text[open_brace:]


def collect_methods(paths: list[Path]) -> list[Method]:
    methods: list[Method] = []
    for path in paths:
        text = path.read_text(errors="replace")
        for match in METHOD.finditer(text):
            name = match.group("name")
            if name in {"if", "for", "foreach", "while", "switch", "catch", "using", "lock", "try", "get", "set"}:
                continue
            brace = text.index("{", match.end() - 1)
            methods.append(Method(name, path, text[: match.start()].count("\n") + 1, _body_from(text, brace)))
    return methods


def _calls(body: str, known: set[str]) -> set[str]:
    return {name for name in re.findall(r"\b(\w+)\s*\(", body) if name in known}


def reachable_from_frame(methods: list[Method]) -> dict[str, list[str]]:
    """Method name -> the call path from a per-frame entry point that reaches it."""
    by_name: dict[str, list[Method]] = {}
    for method in methods:
        by_name.setdefault(method.name, []).append(method)
    known = set(by_name)

    paths: dict[str, list[str]] = {}
    frontier = [(name, [name]) for name in PER_FRAME_ENTRIES & known]
    for name, path in frontier:
        paths[name] = path
    while frontier:
        name, path = frontier.pop()
        for method in by_name.get(name, []):
            for callee in _calls(method.body, known):
                if callee in paths or callee in PER_FRAME_ENTRIES:
                    continue
                paths[callee] = path + [callee]
                frontier.append((callee, paths[callee]))
    return paths


def check(root: Path) -> list[str]:
    sources = sorted(p for p in root.glob("tools/**/*.cs") if "/refs/" not in str(p))
    if not sources:
        return []
    methods = collect_methods(sources)
    frame_paths = reachable_from_frame(methods)
    problems: list[str] = []
    for method in methods:
        route = frame_paths.get(method.name)
        if route is None:
            continue
        annotated = bool(ANNOTATION.search(method.body))
        offenders: list[tuple[str, str]] = [
            (m.group(1), "scene-wide search") for m in SCENE_SEARCHES.finditer(method.body)
        ]
        if method.name in PER_FRAME_ENTRIES:
            offenders += [(m.group(1), "allocation on every frame") for m in ALLOCATIONS.finditer(method.body)]
        if not offenders or annotated:
            continue
        seen: set[str] = set()
        for token, kind in offenders:
            if token in seen:
                continue
            seen.add(token)
            problems.append(
                f"{method.path}:{method.line}: {method.name}(): {kind}: {token} "
                f"[reached by {' -> '.join(route)}]. "
                f"Bound it, move it off the frame path, or state the bound: "
                f"// lint:per-frame bounded by <what stops it>"
            )
    return problems


def fingerprint(problem: str) -> str:
    """file:method:token, without a line number, so edits above a site do not churn the baseline."""
    path, _, rest = problem.partition(":")
    method = rest.split(": ", 1)[1].split("(", 1)[0] if ": " in rest else "?"
    token = rest.split("search: ")[-1].split(" ")[0] if "search: " in rest else "?"
    return f"{Path(path).name}:{method}:{token}"


def load_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")}


def main(argv: list[str]) -> int:
    positional = [a for a in argv[1:] if not a.startswith("--")]
    root = Path(positional[0]) if positional else Path(__file__).resolve().parent.parent
    baseline_path = root / "tools/perframe-baseline.txt"
    problems = check(root)
    marks = {fingerprint(p) for p in problems}
    if "--write-baseline" in argv:
        baseline_path.write_text(
            "# Scene-wide searches on frame paths that predate tools/check_perframe_work.py.\n"
            "# Each line is a site still to be judged: bound it, move it off the frame path, or\n"
            "# annotate it with its bound - then delete the line. Nothing new may be added here.\n"
            + "".join(f"{mark}\n" for mark in sorted(marks))
        )
        print(f"per-frame work: baseline written with {len(marks)} known sites")
        return 0
    baseline = load_baseline(baseline_path)
    fresh = [p for p in problems if fingerprint(p) not in baseline]
    for problem in fresh:
        print(problem, file=sys.stderr)
    if fresh:
        print(f"per-frame work: {len(fresh)} NEW unbounded operation(s) on a frame path", file=sys.stderr)
        return 1
    stale = baseline - marks
    print(f"per-frame work: no new violations ({len(marks & baseline)} known sites still to judge"
          + (f", {len(stale)} baseline entries now stale and removable" if stale else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
