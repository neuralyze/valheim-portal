#!/usr/bin/env python3
"""Run a server console command through RCON `consoleCommand` and return what
the console actually printed.

Why this is not one line: ValheimRcon's `consoleCommand` answers only
"Command '<x>' executed." -- MEASURED -- and the command's real output goes to
the server's own console sink, which surfaces in the container log as
`Console: <text>`.  So the only way to read a console command's result
headlessly is to mark the log, run the command, and read the tail back.

`docker logs --since` is the marker: the container log is timestamped by
supervisord at one-second resolution, so a fresh `--since` timestamp plus a
short settle is enough to isolate one command's output.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rcon import CONTAINER, Rcon  # noqa: E402

CONSOLE_RE = re.compile(r"Console: (.*)$")
NOISE = re.compile(
    r"Command completed:|Command '.*' executed\.|DropCleaner|Valheim Rcon\]|"
    r"^Console: \[(Message|Info|Warning)"
)


def log_cursor() -> str:
    """Nanosecond-ish cursor docker accepts for --since."""
    return subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%S.%NZ"],
                          capture_output=True, text=True, check=True).stdout.strip()


def logs_since(cursor: str) -> list[str]:
    out = subprocess.run(["sudo", "-n", "docker", "logs", "--since", cursor, CONTAINER],
                         capture_output=True, text=True)
    return (out.stdout + out.stderr).splitlines()


def console_output(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        m = CONSOLE_RE.search(line)
        if not m:
            continue
        text = m.group(1).strip()
        if not text or NOISE.search(text) or NOISE.search(line):
            continue
        result.append(re.sub(r"<[^>]+>", "", text))
    return result


def run_console(rc: Rcon, cmd: str, settle: float = 1.2) -> list[str]:
    cursor = log_cursor()
    time.sleep(0.05)
    rc.console(cmd)
    time.sleep(settle)
    return console_output(logs_since(cursor))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="+")
    ap.add_argument("--settle", type=float, default=1.5)
    args = ap.parse_args()
    cmd = " ".join(args.command)
    with Rcon() as rc:
        for line in run_console(rc, cmd, args.settle):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
