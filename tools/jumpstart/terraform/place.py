#!/usr/bin/env python3
"""Execute a `spawn` plan from tools/jumpstart/blueprints/to_rcon_plan.py.

One command per RCON round trip, deliberately: ValheimRcon echoes a detail line
per spawned object and MEASURED, a response over ~4050 bytes is truncated -- and
worse, a large response floods the server's console log pipeline and has WEDGED
this server (a wedged server is SIGKILLed on stop, losing everything since the
last save).  So the responses stay one object long and the plan is paced.

Failures are counted, not swallowed: a prefab the server cannot resolve answers
"Cannot find prefab <name>" and that is the interesting half of the result.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rcon import Rcon  # noqa: E402

OK_RE = re.compile(r"Spawned (\d+) objects?:", re.I)
ID_RE = re.compile(r"Id: (\d+:\d+)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--delay", type=float, default=0.02)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--save", action="store_true", help="issue RCON save at the end")
    ap.add_argument("--ids", help="write the spawned ZDO ids here, one per line")
    args = ap.parse_args()

    lines = [ln.strip() for ln in Path(args.plan).read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    if args.limit:
        lines = lines[:args.limit]

    placed = 0
    ids: list[str] = []
    failures: Counter[str] = Counter()
    examples: dict[str, str] = {}
    with Rcon(timeout=30.0) as rc:
        for i, cmd in enumerate(lines):
            reply = rc.command(cmd).strip()
            if OK_RE.search(reply):
                placed += 1
                found = ID_RE.search(reply)
                if found:
                    ids.append(found.group(1))
            else:
                key = reply.splitlines()[0][:120] if reply else "<empty response>"
                failures[key] += 1
                examples.setdefault(key, cmd)
            if args.delay:
                time.sleep(args.delay)
            if (i + 1) % 100 == 0:
                print(f"  ... {i + 1}/{len(lines)} attempted, {placed} placed", flush=True)
        if args.save:
            print("  save:", rc.command("save").strip())

    print(f"{args.plan}: attempted {len(lines)}, placed {placed}, failed {len(lines) - placed}")
    for key, n in failures.most_common():
        print(f"  {n} x {key}   (e.g. {examples[key]})")
    if args.ids:
        Path(args.ids).write_text("\n".join(ids) + "\n")
        print(f"  ids -> {args.ids} ({len(ids)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
