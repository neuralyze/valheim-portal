#!/usr/bin/env python3
"""Run a server console command through RCON `consoleCommand` and return what
the console actually printed.

Why this is not one line: ValheimRcon's `consoleCommand` answers only
"Command '<x>' executed." -- MEASURED -- and the command's real output goes to
the server's own console sink, which surfaces in the container log as
`Console: <text>`.  So the only way to read a console command's result
headlessly is to mark the log, run the command, and read the tail back.

The log is NOT sliced by a clock. It is sliced by ValheimRcon's OWN ECHO. Two
measured facts make the clock unusable and the echo exact:

  * supervisord stamps the container log at ONE-SECOND resolution, so a
    nanosecond `--since` cursor can be LATER than the stamp on output produced
    after it, and `docker logs` drops that output. MEASURED: `objects_count` at
    `pre-yagluth/plains-farm-base` printed its whole prefab table and its
    `Total:` line, and a `--since 17:57:35.087Z` read returned nothing.
  * Truncating the cursor to the second is NOT enough either. MEASURED at
    22:59:20Z: `docker logs --since 2026-09-15T22:50:00Z` returned 8,061 lines
    and `--since 2026-09-15T22:59:20Z` returned 0, for a command whose output
    was in those 8,061. docker's own stamps lag the write by seconds, so no
    absolute cursor is safe.

The echo has neither problem. `consoleCommand` makes the server log
`Command '<cmd>' executed.` AFTER the command's console output, MEASURED and
visible in that order in the container log. So the command's output is exactly
the `Console:` lines between the PREVIOUS echo and THIS command's echo, which
is clock-independent and needs no window tuning.

A read that cannot find its own echo raises. The caller must be able to tell
"the command printed nothing" from "I could not find the output", because the
second answer read as the first is how a healthy world got restarted tonight.

THREE CAUSES OF AN EMPTY CONSOLE READ, AND HOW TO TELL THEM APART. All three
happened on Ulfsland on 2026-09-15 and two of them were diagnosed as the third.
Check in this order; each signature is independent evidence, not a guess.

  1. GENUINE WEDGE -- the Unity main thread is blocked, so RCON does not
     answer at all: the connection is refused or times out. A `findObjects`
     listing over ~1,900 pieces does this, because ValheimRcon's
     `RconProxy.HandleCommandAsync` calls `Log.Message` on the FULL reply text
     from the main thread before `ValidatePayloadLength` truncates it.
     Recovery: restart the container.
  2. CLOCK RACE -- RCON answers, and the container log FILE IS STILL GROWING,
     but the read returns nothing. This was `log_cursor()` generating a
     nanosecond `--since` cursor against a log supervisord stamps at
     one-second resolution; docker's own stamps also lag the write, so no
     absolute cursor is safe. Fixed above by slicing on the command's echo
     instead of on any clock. Recovery: none needed.
  3. DEAD SINK -- RCON answers FAST (MEASURED: 88 ms) and status.json keeps
     updating, but the container's stdout has stopped entirely: the json log
     file's mtime and its last record both freeze. MEASURED: the final record
     was 2026-09-15T22:57:35.277026665Z, the echo of an
     `objects_count id=* ignore=_*` over a 33 m radius in a freshly generated
     Plains zone, and nothing was written for the following six minutes.
     `objects_count` writes its table to the CONSOLE SINK rather than to the
     RCON reply, so it bypasses the bounded `Log.Message` path entirely and
     puts a large block straight on stdout. MEASURED trigger size: that one
     table began `Total: 6149` and continued with one line PER PREFAB.
     Recovery: NOT a restart. It is a circular wait between supervisord
     (blocked in `sendto()` to `/dev/log`) and syslogd (blocked writing its
     stdout to the pipe only supervisord drains), and draining that pipe
     clears it in about 2 seconds without touching the game. See
     `rcon.log_sink_state` and `rcon.recover_log_sink`.
     Mitigation remains: never ask for an unbounded table. But note that
     looping ONE NAMED PREFAB at a time over a generated zone moves the SAME
     number of rows through the same chain plus an extra echo per call -- the
     fix is to ask only about the prefabs you PLACED, or to not ask at all.

The distinguishing measurement is cheap and should be taken before any
restart: does the log FILE still grow, and does RCON answer? Growing plus
answering is (2); frozen plus answering is (3) -- call
`rcon.recover_log_sink()`; not answering is (1).
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


WINDOW_S = 120
ECHO_RE = re.compile(r"Command '(?P<cmd>.*)' executed\.")


class OutputNotFound(RuntimeError):
    """The command's own echo is not in the log window, so its output is unknown.

    Deliberately NOT an empty list. `clearing/clear.py::box_query` raising on a
    missing `Total:` line is what kept a clock race to two lost minutes instead
    of a removal aimed at nothing; this is the same refusal one layer down.
    """


def logs_window(seconds: int = WINDOW_S) -> list[str]:
    """The last `seconds` of container log, by docker's own relative window.

    Relative rather than absolute: `--since 5m` is resolved by docker against
    the same clock it stamped the records with, so it cannot disagree with
    itself the way an externally-generated timestamp can.
    """
    out = subprocess.run(["sudo", "-n", "docker", "logs", "--since", f"{seconds}s", CONTAINER],
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


def slice_for(lines: list[str], cmd: str) -> list[str]:
    """The `Console:` lines belonging to the LAST run of `cmd` in this window.

    THE ECHO ARRIVES AS A CLUSTER, NOT AS ONE LINE, and taking the last line of
    it is a measured bug. ValheimRcon's completion is logged twice -- once
    directly and once re-emitted through the server's own console sink -- so the
    tail of a single `objects_count` run reads:

        Console: BH_Pickable_ThorsToadstool: 2
        [Message:Valheim Rcon] ... Command completed: consoleCommand
        Command 'objects_count ...' executed.
        Console: [Message:Valheim Rcon] ... Command completed: consoleCommand
        Command 'objects_count ...' executed.

    Anchoring on the LAST echo and walking back to the previous echo therefore
    lands between the two copies, and the slice contains one noise line and no
    output: `run_console` returned [] while the log plainly held the table.
    MEASURED by `BulkPlace` on `objects_count id=* ignore=_* pos=-4672,-700
    max=25`. So the anchor is the FIRST echo of the trailing cluster, and the
    slice runs back from there to the previous DIFFERENT command's echo.
    """
    hits = [i for i, line in enumerate(lines)
            if (m := ECHO_RE.search(line)) and m.group("cmd") == cmd]
    if not hits:
        raise OutputNotFound(
            f"no `Command '{cmd}' executed.` echo in the last {WINDOW_S}s of container log; "
            f"the command's output is UNKNOWN, not empty")
    # Walk back over the trailing run of echoes for THIS command, skipping the
    # "Command completed:" noise that sits between the duplicate copies.
    end = hits[-1]
    for i in range(len(hits) - 2, -1, -1):
        gap = lines[hits[i] + 1:end]
        if all(NOISE.search(g) for g in gap):
            end = hits[i]
        else:
            break
    start = 0
    for i in range(end - 1, -1, -1):
        if ECHO_RE.search(lines[i]):
            start = i + 1
            break
    return console_output(lines[start:end])


def run_console(rc: Rcon, cmd: str, settle: float = 1.2) -> list[str]:
    rc.console(cmd)
    time.sleep(settle)
    return slice_for(logs_window(), cmd)


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
