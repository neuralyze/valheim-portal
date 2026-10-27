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
import os
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

# Only short, concrete-prefab counts have a bounded two-line answer.  Keep the
# aggregate log burst small too; this is not RCON payload batching/pipelining.
COUNT_BATCH_MAX = 8
_COUNT_QUERY_RE = re.compile(
    r"objects_count id=(?P<ident>[A-Za-z0-9_]+)"
    r"(?: ignore=[A-Za-z0-9_*,.-]+)?"
    r" pos=-?\d+\.\d{2},-?\d+\.\d{2} max=\d+\.\d{2}")
_COUNT_TOTAL_RE = re.compile(r"Total:?\s*(\d+)\s*")
_COUNT_ROW_RE = re.compile(r"(\S+):\s*(\d+)\s*")
_COUNT_LOG_ROTATED = "__jumpstart_count_log_interval_lost__"


class OutputNotFound(RuntimeError):
    """The command's own echo is not in the log window, so its output is unknown.

    Deliberately NOT an empty list. `clearing/clear.py::box_query` raising on a
    missing `Total:` line is what kept a clock race to two lost minutes instead
    of a removal aimed at nothing; this is the same refusal one layer down.
    """

class UnrecognizedVerb(RuntimeError):
    """`Terminal` does not know this verb, so the command DID NOT RUN.

    THE ELEVENTH INSTANCE OF THE KEY CLASS, AND IT WAS FOUND BY THE FIRST
    SEED THAT WAS NEVER ULFSLAND.
    ==========================================================================
    `replay.Server.require_cheats` declares that a write session's writes can
    land, and keys that on ONE thing: `Terminal.m_cheat`.  That is a SUBSET of
    what the quantity actually depends on.  `Terminal.TryRunCommand` has TWO
    ways to refuse a command while `RconProxy` still returns
    `Command '<cmd>' executed.` over the socket:

        registered, IsCheat=true, m_cheat false
            -> "'<verb>' is not valid in the current context."     REQUIRE_CHEATS
        NOT REGISTERED AT ALL (the plugin providing it is not loaded)
            -> "'<verb>' is not a recognized command. ..."         NOTHING

    MEASURED 2026-09-23 driving stage 12 on Fresh42: `require_cheats()`
    returned `enabled by this session`, and step 2 `site_ground` then reported
    **rc 0 on 13 of 13 units** while EVERY ONE of its 33 `spawn_object
    _TerrainCompiler` commands came back `'spawn_object' is not a recognized
    command.`  Not one cubic metre of terrain moved, 13 pads read as levelled,
    and the build walked on to step 3.  `spawn_object` and `deleteObjects` are
    registered by the `World_Edit_Commands` plugin, which Ulfsland's server
    carries among 100 mods a human installed and a fresh seed's server does
    not carry at all -- so this precondition had never been declared either,
    in exactly the way `devcommands` had never been declared.

    So the refusal lives HERE, at the one place every driver in this tree
    reads console output through -- `clearing/clear.py`, `terraform/flatten.py`,
    `terraform/sites.py` and `terraform/stock.py` go straight to `Rcon` and
    never open a `LiveBuilder`, so a guard that lived only in the session
    would name a SUBSET of the writers a second time.
    """


# HOW A TERMINAL SAYS "I DO NOT KNOW THIS VERB", AND THERE ARE TWO OF THEM.
# Matched on the CONSOLE line, never on the socket reply: the socket reply is
# `InvokeConsoleCommand.OnHandle`'s own echo and never carries the console's
# answer (see rcon.py's module docstring).
#
# BOTH WORDINGS ARE MEASURED, ON THE SAME SERVER, TWENTY SECONDS APART.
# 2026-09-23 on Fresh42, probing verbs one at a time:
#     Console: 'spawn_object' is not a recognized command. Type 'help' ...
#     Console: Unknown command 'deleteObjects'. Type 'help' ...
# The first is vanilla `Terminal.TryRunCommand`; the second is the Terminal
# that `Server_devcommands` installs over it. A checker that knew only the
# vanilla sentence would pass every unregistered verb on every server that
# has the mod every other verb here needs -- a key naming a SUBSET of the
# ways the answer is spelled.
UNKNOWN_MARKERS = ("is not a recognized command", "Unknown command")
NOT_RECOGNIZED = UNKNOWN_MARKERS[0]
# The other refusal in the same family: registered, but IsCheat with cheats
# off.  `replay.Server.require_cheats` owns the response to this one; it is
# named here so both are spelled in one place and neither is re-derived.
NOT_VALID_HERE = "is not valid in the current context"



# THE CONSOLE OUTPUT IS READ FROM THE CONTAINER'S OWN FILE, NOT FROM
# `docker logs`, AND THAT IS THE FOURTH FAILURE MODE OF THIS SUBSYSTEM.
#
# MEASURED twice on 2026-09-16, once mid-census on T4 and once 42 seconds into
# T4's clearing emit on a FRESHLY RESTARTED container: `docker logs` stopped
# returning anything at all while the game answered `players` in 34 ms, both
# supervisord and syslogd sat in their normal event loops (`do_poll` /
# `__skb_wait_for_more_packets`, so not the drainable circular wait), and the
# container's json log file simply stopped growing -- 44,891,528 bytes at
# 08:44:46, and nothing after it.  The read end belongs to containerd-shim,
# which was in `futex_wait_queue`, so the break is DOWNSTREAM of everything
# this project can reach: not the game, not the sink, not drainable, and a
# restart bought seven minutes and four calls before it happened again.
#
# AND THE SAME CONTENT WAS AVAILABLE ALL ALONG.  supervisord captures the
# server's stdout into `/var/log/supervisor/valheim-server-stdout---*.log`
# before forwarding a copy to its own stdout, and MEASURED at the moment
# `docker logs` was dead that file was still growing -- same `Console:` lines,
# same `Command '<cmd>' executed.` echoes, minus the syslog prefix.  So the
# console path's dependency on the docker log stream was never necessary, and
# with it gone the console is as robust as the socket.
#
# THE WINDOW IS A BYTE OFFSET, NOT A CLOCK AND NOT A LINE COUNT.  The offset
# is taken BEFORE the command is sent and the read starts there, so the slice
# cannot contain a stale echo of the same command from an earlier call -- which
# a line-based tail can, and which would attribute one cylinder's census to
# another.  If the file rotated in between (supervisord rotates at ~1 MB) the
# size goes DOWN, and the reader falls back to the rotated file plus the new
# one rather than reading a wrong offset.
SUP_LOG_GLOB = "/var/log/supervisor/valheim-server-stdout---supervisor-*.log"

# CONSOLE_LOG_DEPENDS_ON: WHERE THIS SERVER WRITES ITS STDOUT.  Nothing else.
#
# Both readers above went through `docker exec <container>`, which keys the
# console's output on "the server runs in a supervisord container" -- a
# SUPERSET of what the output depends on, which is only "the server's stdout
# is in a file this process can read".  MEASURED 2026-09-23 on the fresh seed
# Fresh42, whose server is a bare `valheim_server.x86_64` on the host: the
# first `zones_generate` of stage 10 sent fine over the socket and then
# `run_console` raised OutputNotFound -- "the command's output is UNKNOWN,
# not empty" -- for every command, because `docker exec
# valheim-server-Fresh42` names nothing.  A world that is not in a container
# had no console at all, so no fresh seed could ever be surveyed.
#
# `VALHEIM_SERVER_LOG` names the file directly.  Unset, every existing caller
# takes the container path it was measured with, byte offset and rotation
# fallback unchanged -- this is one branch on the TRANSPORT, not a second
# reader.
HOST_LOG = os.environ.get("VALHEIM_SERVER_LOG", "")
LOG_GLOB = HOST_LOG or SUP_LOG_GLOB


def _exec(script: str, timeout: float = 30.0) -> str:
    if HOST_LOG:
        out = subprocess.run(["bash", "-c", script], capture_output=True,
                             text=True, timeout=timeout)
        return out.stdout
    out = subprocess.run(["sudo", "-n", "docker", "exec", CONTAINER, "bash",
                          "-c", script], capture_output=True, text=True,
                         timeout=timeout)
    return out.stdout


def sup_log_size() -> tuple[str, int] | None:
    """(path, byte size) of the container's live server-stdout log."""
    got = _exec(f"f=$(ls -t {LOG_GLOB} 2>/dev/null | head -1); "
                f"[ -n \"$f\" ] && echo \"$f $(wc -c < \"$f\")\"").strip()
    if not got:
        return None
    path, _, size = got.rpartition(" ")
    try:
        return path.strip(), int(size)
    except ValueError:
        return None


def sup_log_since(path: str, offset: int, *,
                  require_unrotated: bool = False) -> list[str]:
    """Everything appended after `offset`; count batches refuse lost cursors."""
    if require_unrotated:
        got = _exec(
            f"sz=$(wc -c < '{path}' 2>/dev/null || echo 0); "
            f"if [ \"$sz\" -ge {offset} ]; then "
            f"  tail -c +{offset + 1} '{path}' 2>/dev/null; "
            f"else echo '{_COUNT_LOG_ROTATED}'; fi")
        if _COUNT_LOG_ROTATED in got.splitlines():
            raise OutputNotFound(
                "count query log rotated past its byte cursor; the batch's "
                "output is UNKNOWN, not empty")
        return got.splitlines()
    got = _exec(
        f"sz=$(wc -c < '{path}' 2>/dev/null || echo 0); "
        f"if [ \"$sz\" -lt {offset} ]; then "
        f"  cat '{path}.1' 2>/dev/null; cat '{path}' 2>/dev/null; "
        f"else tail -c +{offset + 1} '{path}' 2>/dev/null; fi")
    return got.splitlines()


def logs_window(seconds: int = WINDOW_S) -> list[str]:
    """The last `seconds` of container log, by docker's own relative window.

    Kept as the FALLBACK for a container whose supervisor log cannot be read
    (a different image layout, or `docker exec` refused).  Relative rather
    than absolute: `--since 5m` is resolved by docker against the same clock it
    stamped the records with, so it cannot disagree with itself the way an
    externally-generated timestamp can.  What it cannot survive is the docker
    log stream itself stopping, which is what the file reader above is for.
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


# Both spellings, with the verb captured from whichever side it sits on:
#   'spawn_object' is not a recognized command.
#   Unknown command 'deleteObjects'.
_UNKNOWN_RE = re.compile(
    r"'([^']+)'\s+is not a recognized command|Unknown command\s+'([^']+)'")


def unrecognized_verbs(out: list[str]) -> list[str]:
    """Every verb in this console slice the game said it does not know."""
    return [m.group(1) or m.group(2)
            for line in out if (m := _UNKNOWN_RE.search(line))]


class DataEntryMissing(RuntimeError):
    """`spawn_object ... data=<entry>` named a definition this server has not
    loaded, so the object -- and its terrain -- was NOT created.

    THE THIRD WAY THIS SESSION GOT A SUCCESSFUL ECHO FOR A COMMAND THAT MOVED
    NOTHING, MEASURED THE SAME HOUR AS THE OTHER TWO.  Fresh42, stage 12 step
    2, after the unregistered-verb refusal was fixed: `Command 'spawn_object
    _TerrainCompiler from=64,320,0 data=complete_station_hub_z1_5' executed.`
    over the socket, `Error: Can't find data entry: complete_station_hub_z1_5`
    on the console, 18 of 18, and `flatten.py apply` returned 0 for every pad.
    The entries had been written to the world's `config_merged/bepinex/data`
    while the server serving that world watches its own install's
    `<BepInEx>/config/data` -- see `flatten.data_dir`.
    """


NO_DATA_ENTRY = "Can't find data entry:"
_NO_ENTRY_RE = re.compile(re.escape(NO_DATA_ENTRY) + r"\s*(\S+)")


def missing_data_entries(out: list[str]) -> list[str]:
    """Every data definition in this slice the server could not find."""
    return [m.group(1) for line in out if (m := _NO_ENTRY_RE.search(line))]


def check_recognized(cmd: str, out: list[str]) -> list[str]:
    """`out`, or a refusal if the game did not actually run the command.

    A COMMAND THAT DID NOT RUN, WITH THE SOCKET SAYING IT DID, IS THE ONE
    FAILURE SHAPE THIS WHOLE TRANSPORT HAS.  Returning the slice unread is how
    13 pads reported rc 0 with no terrain written, twice, for two different
    reasons -- see `UnrecognizedVerb` and `DataEntryMissing`.  The check is
    here rather than in each driver because the four terrain drivers reach the
    console directly, without a `LiveBuilder`, so a session-level guard names
    a SUBSET of the writers.
    """
    bad = unrecognized_verbs(out)
    if bad:
        raise UnrecognizedVerb(
            f"the game does not know {sorted(set(bad))} -- `{cmd[:120]}` was "
            f"REFUSED by Terminal.TryRunCommand and the RCON socket still "
            f"answered `Command '...' executed.`, so nothing about the world "
            f"may be assumed here. The verb is registered by a PLUGIN; a "
            f"server missing that plugin cannot run this build. Console said: "
            f"{[ln for ln in out if any(k in ln for k in UNKNOWN_MARKERS)][:2]}")
    gone = missing_data_entries(out)
    if gone:
        raise DataEntryMissing(
            f"this server has no data entry {sorted(set(gone))} -- "
            f"`{cmd[:120]}` was accepted by the socket and created NOTHING. "
            f"The definition is loaded by ServerDevcommands' watcher from the "
            f"RUNNING INSTALL's data directory; a file written anywhere else "
            f"is not a definition. Set $VALHEIM_BEPINEX_DATA to that "
            f"directory (terraform/flatten.py::data_dir).")
    return out


def run_console(rc: Rcon, cmd: str, settle: float = 1.2,
                waits: int = 4) -> list[str]:
    """Send a console command and return what the console printed.

    The window is the container log FILE from the byte offset taken before the
    command was sent, so the slice is exactly this run's output.  The echo can
    lag the reply -- `Log.Message` runs on the main thread and the file is
    written by supervisord afterwards -- so a missing echo is re-read a few
    times before it is called missing.  `OutputNotFound` still means UNKNOWN,
    never empty.

    AND A VERB THE GAME DOES NOT KNOW IS `UnrecognizedVerb`, NOT OUTPUT.  The
    socket's `Command '...' executed.` is `InvokeConsoleCommand`'s own echo and
    is returned for a command `Terminal` refused outright, so the ONLY place
    that refusal is visible is this slice -- see `UnrecognizedVerb`.
    """
    got = sup_log_size()
    if got is None:
        rc.console(cmd)
        time.sleep(settle)
        return check_recognized(cmd, slice_for(logs_window(), cmd))
    path, offset = got
    rc.console(cmd)
    last = None
    for i in range(waits):
        time.sleep(settle if i == 0 else 1.0)
        try:
            return check_recognized(cmd, slice_for(sup_log_since(path, offset),
                                                   cmd))
        except OutputNotFound as exc:
            last = exc
            # A MISSING ECHO IS USUALLY THE SINK, SO REPAIR THE SINK AND LOOK
            # AGAIN.  MEASURED on T4's clearing emit: `console("stop")` inside
            # a STAGED op refused because supervisord had entered
            # `unix_wait_for_peer` with syslogd in `pipe_write` -- the
            # drainable circular wait -- and that path, unlike `count_star`,
            # had no repair.  One clearing pass died 1 cylinder in for a
            # condition a two-second host-side drain clears.
            #
            # The drain consumes supervisord's copy of syslogd's stdout, which
            # is what `docker logs` carries -- and NOT this file, which
            # supervisord writes before forwarding.  So draining cannot eat
            # the output being read here.  That is what makes repairing the
            # sink from inside the reader safe rather than self-defeating.
            try:
                import rcon as _rc  # noqa: PLC0415
                _rc.keep_sink_clear(note=f"console echo missing for `{cmd}`")
            except Exception:  # noqa: BLE001 -- diagnosis must not mask this
                pass
    raise last


def count_query_batchable(cmd: str) -> bool:
    """A short scoped query with at most one concrete-prefab result row."""
    return len(cmd) <= 120 and _COUNT_QUERY_RE.fullmatch(cmd) is not None


def _count_slices(lines: list[str], cmds: list[str]) -> list[list[str]]:
    """Attribute each named count to its own unique trailing echo cluster.

    Whole response blocks may reach the file out of request order.  Duplicate
    noise-only echo copies belong to one block; two actual runs of the same
    command in this fresh interval are ambiguous and may not authorize a count.
    """
    clusters: dict[str, list[tuple[int, int]]] = {}
    previous = None
    for i, line in enumerate(lines):
        match = ECHO_RE.search(line)
        if match is None:
            continue
        cmd = match.group("cmd")
        if (previous is not None and previous[0] == cmd
                and all(NOISE.search(ln) for ln in lines[previous[2] + 1:i])):
            start, _end = clusters[cmd][-1]
            clusters[cmd][-1] = (start, i)
        else:
            clusters.setdefault(cmd, []).append((i, i))
        previous = (cmd, clusters[cmd][-1][0], i)

    outputs = []
    for cmd in cmds:
        hits = clusters.get(cmd, [])
        if len(hits) != 1:
            raise OutputNotFound(
                f"count query needs one unique `Command '{cmd}' executed.` "
                f"echo cluster, got {len(hits)}; output is UNKNOWN, not empty")
        end = hits[0][0]
        start = 0
        for i in range(end - 1, -1, -1):
            if ECHO_RE.search(lines[i]):
                start = i + 1
                break
        out = check_recognized(cmd, console_output(lines[start:end]))
        ident = _COUNT_QUERY_RE.fullmatch(cmd).group("ident")
        totals, rows, unexpected = [], [], []
        for line in out:
            total = _COUNT_TOTAL_RE.fullmatch(line)
            row = _COUNT_ROW_RE.fullmatch(line)
            if total:
                totals.append(int(total.group(1)))
            elif row and row.group(1) == ident:
                rows.append(int(row.group(2)))
            else:
                unexpected.append(line)
        if (len(totals) != 1 or unexpected
                or (totals[0] != 0 and rows != totals)
                or (totals[0] == 0 and rows not in ([], [0]))):
            raise OutputNotFound(
                f"count query `{cmd}` has no complete, uniquely attributed "
                f"Total/prefab table: {out!r}; output is UNKNOWN, not zero")
        outputs.append(out)
    return outputs


def run_count_queries(rc: Rcon, cmds: list[str], settle: float = 2.0,
                      waits: int = 4) -> list[list[str]]:
    """Read independent named counts from one fresh log interval, settling once.

    Each request still completes its synchronous socket round trip before the
    next starts.  Wildcards, duplicate commands and large groups are forbidden;
    there is no compound command or combined RCON reply.  Without a file cursor
    the old per-command reader remains necessary to avoid stale-window matches.
    """
    if (not 1 <= len(cmds) <= COUNT_BATCH_MAX
            or len(set(cmds)) != len(cmds)
            or not all(count_query_batchable(cmd) for cmd in cmds)):
        raise ValueError("count batches require 1..8 distinct short named queries")
    got = sup_log_size()
    if got is None:
        return [run_console(rc, cmd, settle=settle, waits=waits) for cmd in cmds]
    path, offset = got
    for cmd in cmds:
        reply = rc.console(cmd).strip()
        if reply != f"Command '{cmd}' executed.":
            raise OutputNotFound(
                f"count query `{cmd}` socket reply did not match its exact "
                f"echo: {reply[:300]!r}; output is UNKNOWN, not zero")
    last = None
    for i in range(max(1, waits)):
        time.sleep(settle if i == 0 else 1.0)
        try:
            return _count_slices(
                sup_log_since(path, offset, require_unrotated=True), cmds)
        except OutputNotFound as exc:
            last = exc
            # As in run_console: draining the forwarded sink cannot consume
            # this supervisor file, and no query is resent while waiting.
            try:
                import rcon as _rc  # noqa: PLC0415
                _rc.keep_sink_clear(note="count batch output missing")
            except Exception:  # noqa: BLE001 -- preserve the UNKNOWN refusal
                pass
    raise last


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
