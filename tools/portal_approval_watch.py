#!/usr/bin/env python3
"""Announce agent-bridge approval decisions as they happen.

Every `world_state` verb waits for an operator click, and `deploy_apply` is
gated on every single invocation by policy. That gate is deliberate and this
does not touch it. What it removes is the *polling*: an agent that requested a
gated verb can wait on this stream instead of asking the operator to report
back that they clicked.

One line per event, so a log watcher can block on a pattern:

    WATCH:START    cursor=<n> interval=<seconds>
    WATCH:PENDING  verb=<verb> id=<id>
    WATCH:SETTLED  <the portal's own words: succeeded / denied / failed>
    WATCH:ERROR    <transport failure, retried on the next tick>

The bridge is read through `GET /api/agent/inbox`, which answers with
`messages`, `awaiting_approval` and a `cursor`. Passing the cursor back is what
makes this a stream rather than a repeated dump.

Usage:
    portal_approval_watch.py [--from-cursor N] [--interval SECONDS] [--once]

`--from-cursor` replays settled history, which is how this is exercised against
a real portal without asking an operator to clear a gate nobody wanted.

Configuration, both optional:
    PORTAL_BASE_URL                 default http://127.0.0.1:18080
    PORTAL_AGENT_BRIDGE_TOKEN_FILE  default /etc/valheim-portal/agent-bridge-token

The token file is root-owned, so a non-root reader falls back to `sudo -n cat`
rather than assuming the caller is root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_TOKEN_FILE = "/etc/valheim-portal/agent-bridge-token"

# The portal states an outcome in its own words; these are the words that mean a
# call is no longer waiting for anybody.
SETTLED = re.compile(r"\b(succeeded|denied|failed|refused)\b", re.IGNORECASE)

# Message text can arrive under any of these; the first non-empty one wins.
TEXT_FIELDS = ("text", "body", "summary", "message", "detail")


def base_url() -> str:
    return os.environ.get("PORTAL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def read_token() -> str:
    """Read the bridge token, falling back to sudo for the root-owned file."""
    path = os.environ.get("PORTAL_AGENT_BRIDGE_TOKEN_FILE", DEFAULT_TOKEN_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except PermissionError:
        pass
    except OSError as error:
        raise SystemExit(f"cannot read {path}: {error}") from error
    result = subprocess.run(["sudo", "-n", "cat", path], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"cannot read {path}: {result.stderr.strip()}")
    return result.stdout.strip()


def summarize(message: dict) -> str:
    """Collapse one inbox message to a single line of the portal's own text."""
    for field in TEXT_FIELDS:
        value = message.get(field)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return " ".join(json.dumps(message, sort_keys=True).split())


def watch_lines(payload: dict, announced: set[str]) -> list[str]:
    """Turn one inbox payload into the lines this has not announced yet.

    `announced` is updated in place, which is what keeps a replayed cursor or a
    still-pending call from being reported on every tick.
    """
    lines: list[str] = []
    for message in payload.get("messages") or []:
        text = summarize(message)
        key = f"settled:{text}"
        if SETTLED.search(text) and key not in announced:
            announced.add(key)
            lines.append(f"WATCH:SETTLED {text[:400]}")
    for call in payload.get("awaiting_approval") or []:
        identifier = str(call.get("id") or "")
        verb = call.get("verb") or call.get("summary") or "?"
        key = f"pending:{identifier}"
        if key not in announced:
            announced.add(key)
            lines.append(f"WATCH:PENDING verb={verb} id={identifier}")
    return lines


def fetch_inbox(token: str, cursor: int | None) -> dict:
    url = f"{base_url()}/api/agent/inbox"
    if cursor is not None:
        url += f"?cursor={cursor}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-cursor", type=int, default=None,
                        help="replay from this cursor instead of only new activity")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="seconds between polls (default 3)")
    parser.add_argument("--once", action="store_true",
                        help="poll a single time and exit")
    args = parser.parse_args(argv)

    token = read_token()
    cursor = args.from_cursor
    if cursor is None:
        # Start at the live edge so a fresh watcher does not replay old decisions.
        cursor = fetch_inbox(token, None).get("cursor")
    print(f"WATCH:START cursor={cursor} interval={args.interval}", flush=True)

    announced: set[str] = set()
    while True:
        try:
            payload = fetch_inbox(token, cursor)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"WATCH:ERROR {error}", flush=True)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue

        for line in watch_lines(payload, announced):
            print(line, flush=True)
        cursor = payload.get("cursor", cursor)

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
