#!/usr/bin/env python3
"""Source-protocol RCON client for the Tristan-ValheimRcon plugin, with the
one guard and the one health probe that this server actually needs.

PLACEMENT IS SAFE TO BATCH.  VERIFICATION IS THE DANGEROUS HALF.
================================================================
That is the opposite of what this repo believed for most of one night, and it
is worth stating first because the wrong version costs an outage.  MEASURED by
disassembling the DEPLOYED `ValheimRcon.dll` (1.6.2, `monodis`, references
resolved from the container's own `BepInEx/core` and `valheim_server_Data`):

  * `RconProxy.HandleCommandAsync` queues EVERY command through
    `ThreadingUtil.RunInMainThread` and awaits the resulting
    `TaskCompletionSource` (IL_0073, IL_0083-IL_008d) before it can return
    anything at all.  The dispatcher is a `MonoBehaviour` whose `Update` drains
    a `ConcurrentQueue`.
  * On resume it does, at IL_00e1-IL_0101:
        Log.Message(string.Concat("Command completed: ", command, "\\n",
                                  result.Text))
    on the FULL, untruncated text.  The completion source is a plain
    `TaskCompletionSource` with no `RunContinuationsAsynchronously`, so that
    continuation runs inline on the thread that completed it -- the Unity MAIN
    thread.  The log write goes to the container's stdout through BepInEx and
    supervisord, and a big write there blocks the main thread.
  * `RconCommandReceiver.ValidatePayloadLength` truncates at 4050 bytes
    (IL_0006 `ldc.i4 4050`) -- but it runs LATER, when the reply packet is
    built.  Truncation never protects the log.

So the wedge is a function of ONE thing: how many bytes land in
`CommandResult.Text`.  And that splits the command surface cleanly in two:

  * `InvokeConsoleCommand.OnHandle` (IL_001f-IL_003c) calls
    `Terminal.TryRunCommand` and then returns exactly
        "Command '" + joined + "' executed."
    It NEVER carries the console command's output.  So a `consoleCommand`
    round trip logs about twice the command text, and the command text is
    capped by this client at `MAX_PAYLOAD`.  A placement batch cannot grow
    with world size.  This is why batched placement is safe.
  * `findObjects` is the opposite: its per-ZDO listing IS `result.Text`, and
    its usage string shows every filter is OPTIONAL --
        findObjects -near <x> <y> <z> <radius> -zone <x> <y> -prefab <prefab>
                    -creator <id> -id <id:userid> -tag <tag> -tag-old <tag>
                    -detailed
    so the bare verb lists the whole world into one `Log.Message`.  ~1,900
    pieces is ~200 KB in a single main-thread write.  That is the outage.
    `logs -lines <big N>` is the only other verb that can be asked for an
    arbitrarily large body; `globalKeys` and the list verbs are bounded by
    things that are not the world size, so they are left alone.

`guard()` therefore REFUSES an unbounded query before a byte reaches the
socket.  A guard that is only a comment gets ignored at 3am.

LIVENESS: MEASURE THE MAIN THREAD, DO NOT INFER IT FROM A SINK
==============================================================
Because `RunInMainThread` is unconditional and awaited, a reply arriving
PROVES the Unity main thread ran at least one frame since the command was
queued.  That makes a bounded RCON round trip a direct instrument.  `probe()`
is it: `players`, whose reply is `Online N` -- MEASURED 8 bytes, RTT 32-35 ms
over n=12 on an idle Ulfsland, min 32 max 35.

The rejected alternative, and why: reading the container log for the
`Connections N ZDOS:` heartbeat.  MEASURED cadence on Ulfsland -- six
consecutive lines exactly 600 s apart (16:24:24, 16:34:24, 16:44:24, 16:54:24,
17:04:24, 17:14:24).  The heartbeat is a TEN MINUTE signal, not the ~30 s it
was assumed to be, so a sound "did a new line appear" test needs a window over
`LOG_LIVENESS_WINDOW_S` -- 21 minutes.  That is unusable inside a build loop,
and every shorter window is a coin flip: a 6 s sample called a healthy server
wedged, and a 40 s sample called a wedged server healthy.  So this module
reads the log for DIAGNOSIS ONLY, after a stall has already been detected by
the probe.

Usage:
    python3 rcon.py "players"
    python3 rcon.py --file cmds.txt --quiet
    python3 rcon.py --probe
"""

from __future__ import annotations

import argparse
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

CONTAINER = "valheim-server-Ulfsland"
CONFIG = Path("/media/big4/projects/game/valheim/Ulfsland/config_merged/bepinex/org.tristan.rcon.cfg")

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

# `RconPacket.MaxPayloadSize`, IL_0006 of `ValidatePayloadLength`: int32 4050.
# Both directions -- an inbound packet over this is rejected by the parser, an
# outbound one is truncated.
MAX_PAYLOAD = 4050

BRIDGE = "consoleCommand "
# `InvokeConsoleCommand.OnHandle` returns Concat("Command '", joined,
# "' executed."), so the REPLY is this many bytes longer than the joined
# command text and is the binding constraint on a batch, not the request.
ECHO_OVERHEAD = len("Command '' executed.")

# MEASURED, not assumed: see the module docstring.  Six consecutive heartbeat
# lines exactly 600 s apart.
HEARTBEAT_PERIOD_S = 600.0
# Two periods plus a minute of jitter.  The only window in which "no new
# heartbeat line" is evidence of anything.  Named so that nobody re-derives a
# 30 s version of it.
LOG_LIVENESS_WINDOW_S = 2 * HEARTBEAT_PERIOD_S + 60.0

# MEASURED from the IL, verb by verb, because a guard that refuses harmless
# commands gets switched off:
#   * `findObjects` scales with world ZDO count and every filter in its usage
#     string is optional -- the only truly unbounded verb.  Conditionally
#     allowed below.
#   * `logs` copies BepInEx's `LogOutput.log` and returns its last N lines,
#     N from `-lines` and DEFAULT 5 (IL_0028 `ldc.i4.5`).  Bare `logs` is
#     harmless; `logs -lines 50000` is not.
#   * `globalKeys`, `adminlist`, `banlist`, `permitted`, `players`,
#     `showContainer` are bounded by things that are not the world size, so
#     they are NOT guarded.  Guarding them would have broken jumpstart.py's
#     `globalKeys` read for no measured reason.
LOG_LINES_CAP = 200
# The largest `-near` radius an UNSCOPED `findObjects` may ask for.  See the
# reasoning in `guard()`: 8 m of half-extent is 256 m2, ~85 objects at the
# densest density measured anywhere a road corridor touches, ~5 KB of reply.
UNSCOPED_NEAR_MAX_M = 8.0


class MainThreadStalled(RuntimeError):
    """The probe did not come back inside its deadline.

    Raised only by `probe()`.  Because the reply is main-thread-gated by
    construction, this means the Unity main thread has not run a frame in that
    long -- the wedge, or something equally worth stopping for.
    """


class UnboundedQuery(ValueError):
    """A command whose reply would be logged in full on the main thread."""


def payload_size(text: str) -> int:
    """Bytes `RconPacket.GetPayloadSize` will count for a body string."""
    return len(text.encode("utf-8"))


def guard(cmd: str) -> None:
    """Refuse a command whose reply is unbounded.  Raises `UnboundedQuery`.

    Checked on the NATIVE verb only.  A console command's output does not
    reach `CommandResult.Text` at all (`InvokeConsoleCommand.OnHandle` returns
    its own echo), so `consoleCommand findObjects ...` is a different animal --
    it floods the server console, which is a separate sink, and that is
    `console.py`'s problem.  What this stops is the native listing that goes
    straight into `Log.Message`.
    """
    verb = cmd.strip().split(" ", 1)[0].lower()
    if verb == "findobjects":
        low = cmd.lower()
        near = re.search(r"-near\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+"
                         r"([\d.]+)", cmd)
        if near is None:
            raise UnboundedQuery(
                "findObjects must be RADIUS-BOUNDED: every filter in its "
                "usage string is optional, so the unscoped form lists the "
                "entire world into one main-thread log write. Use: "
                "findObjects -near <x> <y> <z> <radius> [-prefab <prefab>]")
        # PREFAB-SCOPED, OR SMALL ENOUGH THAT THE SCOPE IS THE BOX.
        #
        # The hazard this guard exists for is bytes in `CommandResult.Text`,
        # which `RconProxy` logs IN FULL on the Unity main thread -- ~200 KB
        # for the whole world is the measured outage.  A prefab filter is a
        # PROXY for boundedness, not the thing itself, and treating the proxy
        # as the rule cost this project its cheapest census: the corridor has
        # to be censused by NAME, 139 names per region, because the one call
        # that answers "what is in this box" was refused -- and the name list
        # then has to come from `objects_count`, whose table goes through the
        # container console sink, which is the subsystem that has now failed
        # four times in this session and takes a 7-minute container restart
        # with it.
        #
        # So the bound is stated in the units of the hazard.  `-near` is a
        # CUBE of half-extent r, and MEASURED over T10's and T4's corridors
        # the densest 32 m cell holds 334 objects, i.e. ~0.33 objects per
        # square metre at the worst place a road touches.  A row is ~62 bytes
        # (MEASURED: 24 rows = 1,404 bytes, 47 = 3,040), so an unscoped box of
        # half 8 m -- 256 m2 -- is ~85 objects and ~5 KB at that density, two
        # orders below the outage and recoverable; half 20 m would be 33 KB
        # and is refused.  The caller still subdivides on the `Found n
        # objects` header, so the bound is a ceiling on ONE reply rather than
        # a claim about what the answer will be.
        if "-prefab" not in low and float(near.group(4)) > UNSCOPED_NEAR_MAX_M:
            raise UnboundedQuery(
                f"an unscoped `findObjects -near ... {near.group(4)}` asks for "
                f"every ZDO in a {2 * float(near.group(4)):g} m cube and that "
                f"listing IS `CommandResult.Text`, logged in full on the main "
                f"thread. Either add `-prefab <name>` or bring the radius to "
                f"{UNSCOPED_NEAR_MAX_M:g} m or less, where the measured "
                f"worst-case corridor density is ~5 KB of reply.")
    if verb == "logs":
        asked = re.search(r"-lines\s+(\d+)", cmd)
        if asked and int(asked.group(1)) > LOG_LINES_CAP:
            raise UnboundedQuery(
                f"`logs -lines {asked.group(1)}` returns that many log lines "
                f"as CommandResult.Text, which Log.Message then writes in "
                f"full on the Unity main thread. Cap is {LOG_LINES_CAP}; for "
                f"more, read the container log directly.")
    if payload_size(cmd) > MAX_PAYLOAD:
        raise UnboundedQuery(
            f"request is {payload_size(cmd)} bytes, over the "
            f"{MAX_PAYLOAD}-byte payload cap; ValheimRcon's parser rejects it")


def container_ip(name: str = CONTAINER) -> str:
    """Resolve the bridge IP.  It CHANGES on every stop/start because hostops
    recreates the network, so it is resolved fresh rather than cached."""
    out = subprocess.run(
        ["sudo", "-n", "docker", "inspect", name, "--format",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, text=True, check=True,
    )
    ip = out.stdout.strip()
    if not ip:
        raise RuntimeError(f"no bridge IP for {name}; is it running?")
    return ip


def rcon_credentials() -> tuple[int, str]:
    text = CONFIG.read_text()
    port = int(re.search(r"^Port\s*=\s*(\d+)", text, re.M).group(1))
    password = re.search(r"^Password\s*=\s*(\S+)", text, re.M).group(1)
    return port, password


def tail_container_log(lines: int = 20, name: str = CONTAINER) -> list[str]:
    """Last `lines` of the container log.  DIAGNOSIS ONLY -- never liveness.

    See `LOG_LIVENESS_WINDOW_S`: the log's own heartbeat is a 600 s signal, so
    absence of output over any loop-sized window means nothing.  Presence of a
    specific line after a detected stall means something.
    """
    out = subprocess.run(
        ["sudo", "-n", "docker", "logs", "--tail", str(lines), name],
        capture_output=True, text=True)
    return (out.stdout + out.stderr).splitlines()


# ---------------------------------------------------------------------------
# The SECOND stall, which is not the wedge and needs a different instrument
# ---------------------------------------------------------------------------
# MEASURED on Ulfsland at 22:57:35 UTC: the container's stdout stopped
# entirely for six minutes while RCON round trips stayed at 88 ms and
# status.json kept updating.  Inside the container:
#
#     pid 29  supervisord      state S  wchan unix_wait_for_peer
#     pid 30  syslogd          state S  wchan pipe_write
#     pid 170 valheim_server   state Ssl, NOT blocked
#
# From /proc/net/unix, syslogd's fd 0 is the `/dev/log` SOCK_DGRAM socket and
# supervisord holds a DGRAM socket to it; pipe 15901491 is syslogd's stdout
# and its ONLY reader is supervisord's fd 7.  So it is a circular wait:
# supervisord blocks in `sendto()` to /dev/log because syslogd's datagram
# receive queue is full, and syslogd blocks writing its stdout to the pipe
# only supervisord drains, which supervisord cannot drain while it is stuck in
# the sendto.  Neither side has a timeout, so it never clears itself.
#
# The trigger, MEASURED by draining 8,084 bytes out of that pipe and reading
# them: one `objects_count id=* ignore=_*` over a freshly generated Plains
# zone, whose table began `Total: 6149` and continued with one line PER PREFAB
# (`rug_fur: 135`, `Rock_4_plains: 6`, `sign: 55`, ...).  Hundreds of separate
# syslog datagrams in one burst fill the queue and close the cycle.
#
# This is NOT the wedge `guard()` prevents.  The wedge is `Log.Message`
# writing an unbounded `CommandResult.Text` on the Unity main thread.  This is
# the CONSOLE sink: output that never touches `CommandResult.Text` at all, and
# so is invisible to `guard()` and to `probe()`.  A stalled sink does not stop
# the game -- but supervisord is the reader of the game's own stdout pipe, so
# while it is stuck it drains nothing, and sustained console output will fill
# those pipes and then block the writer.  INFERENCE, not measured: that is how
# a sink stall becomes a main-thread wedge.  Which is why this is detected and
# cleared rather than waited out.

_SINK_PROBE_SH = r"""
sup=$(pgrep -x supervisord | head -1)
sys=$(pgrep -x syslogd | head -1)
[ -n "$sup" ] && [ -n "$sys" ] || { echo "MISSING $sup $sys"; exit 0; }
echo "PIDS $sup $sys"
echo "WCHAN $(cat /proc/$sup/wchan 2>/dev/null) $(cat /proc/$sys/wchan 2>/dev/null)"
for n in 0 1 2; do
    echo "SYSFD $n $(readlink /proc/$sys/fd/$n 2>/dev/null)"
done
target=$(readlink /proc/$sys/fd/1 2>/dev/null)
for f in /proc/$sup/fd/*; do
    if [ -n "$target" ] && [ "$(readlink "$f" 2>/dev/null)" = "$target" ]; then echo "READFD $f"; fi
done
"""

# `unix_wait_for_peer` on supervisord together with `pipe_write` on syslogd is
# the signature.  Either alone is a normal momentary state.
SINK_STALL_WCHANS = ("unix_wait_for_peer", "pipe_write")


def _in_container(script: str, name: str = CONTAINER, timeout: float = 30.0) -> str:
    out = subprocess.run(["sudo", "-n", "docker", "exec", name, "bash", "-c", script],
                         capture_output=True, text=True, timeout=timeout)
    return out.stdout + out.stderr


def sink_flowing(name: str = CONTAINER, marker: str | None = None,
                 settle: float = 2.0, window_s: int = 30) -> dict:
    """ACTIVELY prove whether the container's log sink is carrying anything.

    Writes a unique marker to the container's own `/dev/log` with `logger` and
    looks for it in `docker logs`.  Nothing else in this module answers the
    question: ABSENCE of log output proves nothing, because the server's own
    heartbeat period is 600 s (see `LOG_LIVENESS_WINDOW_S`), and a /proc
    snapshot proves nothing either -- MEASURED by SeatCheck on the freshly
    restarted, demonstrably healthy container, `readlink /proc/<syslogd>/fd/1`
    from a `docker exec` context reads EMPTY while the log is plainly flowing,
    so an empty fd readlink is not evidence of a lost socket.  It was a
    corroborating detail in the one broken case and I had promoted it to the
    test, which would have sent the next agent into a needless restart.

    The marker goes through `/dev/log` -> syslogd -> supervisord -> container
    stdout: exactly the path that breaks, and the game process is never
    touched.
    """
    mark = marker or f"sinkprobe-{int(time.time() * 1000)}"
    have = _in_container(
        f"command -v logger >/dev/null && logger -p user.info {mark} "
        f"&& echo HAVE_LOGGER || echo NO_LOGGER", name)
    if "HAVE_LOGGER" not in have:
        return {"flowing": None, "marker": mark, "probe": "unavailable",
                "why": "no `logger` in the container; falling back to the "
                       "/proc signature alone, which cannot tell a healthy "
                       "quiet sink from a dead one"}
    time.sleep(settle)
    out = subprocess.run(
        ["sudo", "-n", "docker", "logs", "--since", f"{window_s}s", name],
        capture_output=True, text=True)
    text = out.stdout + out.stderr
    return {"flowing": mark in text, "marker": mark,
            "probe": f"logger -p user.info {mark} then docker logs --since {window_s}s",
            "window_lines": len(text.splitlines())}


def log_sink_state(name: str = CONTAINER, probe: bool = True) -> dict:
    """WHICH sink failure this is, decided by an ACTIVE measurement first and
    by the /proc signature only to say WHICH failure it is.

    Returns `{"verdict": ..., "stalled": bool, "flowing": bool | None,
    "wchans": (sup, sys), "syslogd_fds": {...}, "read_fds": [...],
    "remedy": str, "sink_probe": {...}}` where `verdict` is one of:

      "healthy"        -- the marker written to `/dev/log` came out of
          `docker logs`.  The sink carries traffic; if a census is blind the
          cause is elsewhere.
      "circular_wait"  -- not flowing, supervisord in `unix_wait_for_peer` AND
          syslogd in `pipe_write`, with supervisord still holding a read end of
          syslogd's stdout pipe.  DRAINABLE by `recover_log_sink` in about
          20 s, no restart, the game untouched.
      "fds_lost"       -- not flowing, supervisord blocked in
          `unix_wait_for_peer`, and no read end of syslogd's stdout anywhere in
          supervisord.  There is nothing for `dd` to drain: a container RESTART
          is the only remedy.
      "blocked_unknown"-- not flowing and neither signature matches.  Named
          rather than folded into either, because "I do not recognise this" is
          a different claim from "this is the drainable one".

    TWO OF THIS INSTRUMENT'S OWN DEFECTS ARE RECORDED HERE, BOTH MINE.

    (1) The verdict replaced a BOOLEAN.  MEASURED on the third outage:
    `stalled` answered False and was RIGHT ABOUT THE QUESTION IT ASKS -- "is
    this the circular wait" -- while being READ as "is the sink healthy".
    Every `objects_count` in the project was blind, `recover_log_sink` no-opped
    and returned that same state, and a no-op that looks like a successful
    recovery is how the next agent spends an hour.

    (2) The first fix then made the SAME mistake one level up: it decided
    `fds_lost` from `readlink /proc/<syslogd>/fd/{0,1,2}` being empty.  MEASURED
    by SeatCheck on the freshly restarted, demonstrably healthy container --
    485 lines in a 120 s window, supervisord in `do_poll.constprop.0` -- those
    readlinks STILL read empty, because reading another process's fd links from
    a `docker exec` context is not permitted to resolve them.  So the empty
    readlink was never evidence of a lost socket; it was a corroborating detail
    in the one broken case that I promoted to the test, and it would have sent
    the next agent into a needless restart.  The fds are still reported, as
    evidence, and they decide nothing.

    Processes are found by name, never by the pids observed once: they differ
    per container and per restart.
    """
    text = _in_container(_SINK_PROBE_SH, name)
    wchans: tuple[str, ...] = ()
    read_fds: list[str] = []
    sys_fds: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("WCHAN "):
            wchans = tuple(line.split()[1:])
        elif line.startswith("READFD "):
            read_fds.append(line.split(None, 1)[1].strip())
        elif line.startswith("SYSFD "):
            parts = line.split(None, 2)
            sys_fds[parts[1]] = parts[2].strip() if len(parts) > 2 else ""
    flow = sink_flowing(name) if probe else {"flowing": None,
                                             "probe": "not requested"}
    sup_blocked = bool(wchans) and wchans[0] == SINK_STALL_WCHANS[0]
    circular = (len(wchans) == 2 and tuple(wchans) == SINK_STALL_WCHANS
                and bool(read_fds))
    if flow["flowing"] is True:
        verdict = "healthy"
    elif circular:
        verdict = "circular_wait"
    elif sup_blocked and not read_fds:
        verdict = "fds_lost"
    elif flow["flowing"] is False:
        verdict = "blocked_unknown"
    else:
        # The probe could not run at all, so the only honest fallback is the
        # signature, and its absence means "not the shape I know" rather than
        # "fine".
        verdict = "circular_wait" if circular else "healthy"
    return {
        "verdict": verdict,
        # Kept, and it now means exactly what its name says: the drainable
        # shape. Callers that tested it keep working and keep being right.
        "stalled": verdict == "circular_wait",
        "flowing": flow["flowing"],
        "wchans": wchans, "syslogd_fds": sys_fds, "read_fds": read_fds,
        "sink_probe": flow,
        "remedy": {
            "healthy": "none; if a census is blind, the cause is elsewhere",
            "circular_wait": "recover_log_sink(), ~20 s, game untouched",
            "fds_lost": ("docker restart -- supervisord is blocked in "
                         "unix_wait_for_peer and holds no read end to drain"),
            "blocked_unknown": ("diagnose before acting: the sink is not "
                                "carrying a marker and neither known "
                                "signature matches"),
        }[verdict],
        "raw": text.strip()}


# ---------------------------------------------------------------------------
# THE SAME SINK, READ AND REPAIRED FROM THE HOST
# ---------------------------------------------------------------------------
#
# WHY A SECOND SET OF FUNCTIONS FOR ONE SUBSYSTEM: everything above reaches
# the container through `docker exec`, and `docker exec` is exactly what
# cannot be trusted while the sink is stalled.  MEASURED tonight, twice: with
# supervisord blocked in `unix_wait_for_peer` the exec-based probe reported
# `fds_lost` -- whose named remedy is a 4-7 minute container restart -- while
# the host-side read of the same two `/proc` entries showed the DRAINABLE
# circular wait, and a sustained read of supervisord's own read end released
# it in UNDER TWO SECONDS with the game process untouched.  A diagnosis that
# costs a restart when the repair costs two seconds is the expensive kind of
# wrong, so the host path is the one a loop should use.
#
# The signature is the pair, not either half: supervisord in
# `unix_wait_for_peer` AND syslogd in `pipe_write`.  syslogd's stdout pipe is
# full because supervisord stopped reading it; supervisord is blocked sending
# to its own log socket.  Neither can move until somebody else reads the pipe.
#
# THE PRICE, stated because it matters to the caller: the bytes drained are
# supervisord's copy of syslogd's stdout, i.e. container log lines that
# `docker logs` would otherwise have carried.  A drain during a
# `consoleCommand` round trip can therefore eat that command's OUTPUT, which
# is the thing `console.py` reads.  That is why this is safe for a
# socket-only census (the echoes are pure cost) and why anything reading
# console output must re-ask after a drain rather than trust the gap.


def _sh(cmd: str, timeout: float = 20.0) -> str:
    out = subprocess.run(["sudo", "-n", "bash", "-c", cmd],
                         capture_output=True, text=True, timeout=timeout)
    return (out.stdout + out.stderr).strip()


def host_sink_pids(name: str = CONTAINER) -> dict:
    """HOST pids of the container's supervisord and syslogd.

    `docker top` reads the host pid namespace, so these are paths under the
    host's own `/proc` and need no exec into a container that may be wedged.
    """
    out = subprocess.run(["sudo", "-n", "docker", "top", name, "-eo",
                          "pid,comm"], capture_output=True, text=True,
                         timeout=30.0)
    pids: dict[str, int] = {}
    for line in (out.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("supervisord", "syslogd"):
            pids.setdefault(parts[1], int(parts[0]))
    return pids


def host_sink_state(name: str = CONTAINER) -> dict:
    """The sink's verdict from the HOST's `/proc`, with no `docker exec`.

    `stalled` is the measured signature above.  `read_fds` are supervisord's
    read ends of syslogd's stdout/stderr pipes, matched by PIPE INODE rather
    than by fd number, because the fd number is not stable across restarts.
    """
    pids = host_sink_pids(name)
    sup, sys_ = pids.get("supervisord"), pids.get("syslogd")
    if not sup or not sys_:
        return {"verdict": "unknown", "why": "no supervisord/syslogd in "
                f"`docker top {name}`", "pids": pids}
    wsup = _sh(f"cat /proc/{sup}/wchan 2>/dev/null")
    wsys = _sh(f"cat /proc/{sys_}/wchan 2>/dev/null")
    targets = [t for t in (_sh(f"readlink /proc/{sys_}/fd/1"),
                           _sh(f"readlink /proc/{sys_}/fd/2")) if t]
    read_fds: list[str] = []
    if targets:
        pat = "|".join(t.replace("[", r"\[").replace("]", r"\]")
                       for t in targets)
        read_fds = [ln for ln in _sh(
            f"for f in /proc/{sup}/fd/*; do t=$(readlink \"$f\" 2>/dev/null); "
            f"if echo \"$t\" | grep -Eq '^({pat})$'; then echo \"$f\"; fi; "
            f"done").splitlines() if ln.startswith("/proc/")]
    stalled = (wsup == "unix_wait_for_peer" and wsys == "pipe_write")
    verdict = ("circular_wait" if stalled and read_fds else
               "fds_lost" if stalled else "healthy")
    return {"verdict": verdict, "stalled": stalled,
            "pids": {"supervisord": sup, "syslogd": sys_},
            "wchans": [wsup, wsys], "syslogd_out_pipes": targets,
            "read_fds": read_fds,
            "remedy": {
                "healthy": "none",
                "circular_wait": ("drain_log_sink_host() -- a sustained read "
                                  "of supervisord's read end, ~2 s"),
                "fds_lost": ("docker restart; supervisord holds no read end "
                             "of syslogd's stdout"),
            }[verdict],
            "method": ("HOST-side /proc read via sudo: wchan of the "
                       "container's supervisord and syslogd (host pids from "
                       "`docker top`), and supervisord's read ends of "
                       "syslogd's stdout/stderr pipes matched by pipe inode. "
                       "No `docker exec`, which is the call that blocks when "
                       "the sink is stalled and makes the in-container probe "
                       "report `fds_lost` spuriously."),
            "tool": "tools/jumpstart/terraform/rcon.py::host_sink_state"}


def drain_log_sink_host(name: str = CONTAINER, seconds: float = 75.0,
                        poll_s: float = 1.0) -> dict:
    """Release the circular wait by reading the pipe from the HOST, and STOP
    as soon as the measurement says it is released.

    A one-shot `dd` is not enough -- the pipe refills while syslogd catches up
    -- so the read is sustained and supervisord's wchan is polled underneath
    it.  MEASURED: released in under 2 s of continuous reading.
    """
    st = host_sink_state(name)
    if st["verdict"] == "healthy":
        return {"drained": False, "why": "not stalled", "state": st}
    if not st.get("read_fds"):
        raise RuntimeError(
            "the sink is stalled and supervisord holds no read end of "
            f"syslogd's stdout: {st}. This is the shape only a restart fixes.")
    fd = st["read_fds"][0]
    sup = st["pids"]["supervisord"]
    t0 = time.time()
    script = (
        f"timeout {int(seconds)} cat {fd} > /dev/null & DP=$!; "
        f"for i in $(seq 1 {int(seconds / max(poll_s, 0.2))}); do "
        f"sleep {poll_s}; w=$(cat /proc/{sup}/wchan 2>/dev/null); "
        f"if [ \"$w\" != unix_wait_for_peer ]; then echo RELEASED_$w; break; "
        f"fi; done; kill $DP 2>/dev/null; wait 2>/dev/null; true")
    out = _sh(script, timeout=seconds + 20.0)
    after = host_sink_state(name)
    return {"drained": True, "fd": fd, "seconds": round(time.time() - t0, 1),
            "released": "RELEASED" in out, "probe_out": out,
            "state": after, "verdict": after["verdict"],
            "tool": "tools/jumpstart/terraform/rcon.py::drain_log_sink_host"}


def keep_sink_clear(name: str = CONTAINER, note: str = "") -> dict:
    """The census's safety valve: measure the sink, drain it if it is in the
    drainable wait, and say what happened.  Raises only for the shape a drain
    cannot fix, so a loop can call this every few dozen calls and keep going.
    """
    st = host_sink_state(name)
    if st["verdict"] == "healthy":
        return {"action": "none", "verdict": "healthy", "note": note}
    if st["verdict"] == "circular_wait":
        got = drain_log_sink_host(name)
        return {"action": "drained", "verdict": got["verdict"],
                "seconds": got["seconds"], "note": note}
    raise RuntimeError(
        f"log sink verdict {st['verdict']} ({note}): {st['remedy']}. "
        f"Host /proc said {st['wchans']} with read_fds {st.get('read_fds')}.")


def recover_log_sink(name: str = CONTAINER, seconds: float = 25.0) -> dict:
    """Break the circular wait by draining the pipe nobody can drain.

    MEASURED recovery on Ulfsland: a sustained read from supervisord's read
    end of syslogd's stdout pipe

        timeout 25 dd if=/proc/<sup>/fd/<n> of=/dev/null bs=65536

    let syslogd finish its blocked write, return to its select loop, drain
    /dev/log, and release supervisord's `sendto()`.  Within 2 SECONDS
    supervisord was back in `do_poll.constprop.0` and syslogd in
    `__skb_wait_for_more_packets` -- both normal event loops -- and the json
    log's mtime began tracking real time again.  Nothing was killed and the
    game process was never touched, so this costs none of the 3-7 minutes a
    container restart costs and none of its risk to the world.

    The price is the few KB of syslogd's own stdout that `dd` consumes, which
    is supervisord's copy of log lines that were already written to
    `/var/log/supervisor/`.

    REFUSES rather than no-ops on `fds_lost`.  MEASURED tonight: this function
    was called on the third outage, saw `stalled=false`, returned that state
    unchanged, and the caller read a successful recovery out of it while every
    `objects_count` in the project stayed blind.  A no-op whose return value is
    indistinguishable from a repair is worse than an exception, so the two
    cases are now told apart and the one this function cannot fix names its
    own remedy.

    Returns the state AFTER the attempt; check `["verdict"] == "healthy"`.
    """
    before = log_sink_state(name)
    if before["verdict"] == "fds_lost":
        raise RuntimeError(
            "log sink is DEAD IN A SHAPE THIS CANNOT REPAIR: syslogd's fds "
            f"0/1/2 readlink to nothing ({before['syslogd_fds']}), so it holds "
            "no sockets and there is no read end for `dd` to drain. This is "
            "NOT the circular wait. The only remedy is `docker restart "
            f"{name}` -- and readiness after it is proved by a `players` "
            "reply, never by a log line, because the log is the thing that "
            f"was broken. Probe said:\n{before['raw']}")
    if before["verdict"] == "healthy":
        return before
    if not before["read_fds"]:
        raise RuntimeError(
            "log sink is stalled but supervisord holds no read end of "
            f"syslogd's stdout; cannot drain it. Probe said:\n{before['raw']}")
    fd = before["read_fds"][0]
    _in_container(
        f"timeout {int(seconds)} dd if={fd} of=/dev/null bs=65536 2>/dev/null; true",
        name, timeout=seconds + 15.0)
    return log_sink_state(name)


class Rcon:
    def __init__(self, host: str | None = None, port: int | None = None,
                 password: str | None = None, timeout: float = 20.0):
        cfg_port, cfg_password = rcon_credentials()
        self.host = host or container_ip()
        self.port = port or cfg_port
        self.password = password or cfg_password
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._id = 0

    def __enter__(self) -> "Rcon":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._send(SERVERDATA_AUTH, self.password)
        pkt_id, pkt_type, body = self._recv()
        if pkt_id == -1:
            raise RuntimeError("rcon auth rejected")
        if pkt_type not in (SERVERDATA_AUTH_RESPONSE, SERVERDATA_RESPONSE_VALUE):
            raise RuntimeError(f"unexpected auth response type {pkt_type}: {body!r}")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _send(self, pkt_type: int, body: str) -> int:
        assert self.sock is not None
        self._id += 1
        payload = struct.pack("<ii", self._id, pkt_type) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._id

    def _read_exact(self, n: int) -> bytes:
        assert self.sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("rcon connection closed mid-packet")
            buf += chunk
        return buf

    def _recv(self) -> tuple[int, int, str]:
        (length,) = struct.unpack("<i", self._read_exact(4))
        payload = self._read_exact(length)
        pkt_id, pkt_type = struct.unpack("<ii", payload[:8])
        body = payload[8:].rstrip(b"\x00").decode("utf-8", "replace")
        return pkt_id, pkt_type, body

    def command(self, cmd: str, deadline: float | None = None) -> str:
        """One synchronous round trip.  `guard()` runs first, always.

        Strictly one command in flight: `RconPeer.TryReceive` reads whatever
        is available into one 4096-byte buffer, parses exactly ONE packet and
        then CLEARS it, so a second queued reply is discarded and a packet
        split across TCP segments is parsed against a zero-padded tail.
        Pipelining silently desynchronises the stream.
        """
        guard(cmd)
        if self.sock is None:
            self.connect()
        if deadline is not None:
            self.sock.settimeout(deadline)  # type: ignore[union-attr]
        try:
            self._send(SERVERDATA_EXECCOMMAND, cmd)
            _, _, body = self._recv()
        finally:
            if deadline is not None and self.sock is not None:
                self.sock.settimeout(self.timeout)
        return body

    def console(self, cmd: str) -> str:
        """Route through ValheimRcon's bridge to the server console, which is
        where Jere Kuusela's admin mods register their commands.

        The reply is only `Command '<cmd>' executed.` -- the console command's
        own output goes to the server console sink and surfaces in the
        container log.  `console.py` is what reads that.
        """
        return self.command(BRIDGE + cmd)

    def probe(self, deadline: float = 15.0) -> float:
        """Main-thread liveness, in seconds of round-trip.

        `players` is native, its reply is `Online N` (MEASURED 8 bytes), and
        like every ValheimRcon command it cannot answer until
        `MainThreadDispatcher.Update` has run the queued action.  A reply
        therefore proves the main thread ran a frame.  No log, no heartbeat,
        no window guessing.

        `deadline` only has to exceed real latency: MEASURED 32-35 ms idle
        (n=12).  A wedged main thread never replies at all, so raising the
        deadline costs detection latency and never correctness, and lowering
        it below a real GC or save pause is the only way to be wrong.
        """
        t0 = time.time()
        try:
            reply = self.command("players", deadline=deadline)
        except socket.timeout as exc:
            raise MainThreadStalled(
                f"no reply to `players` in {deadline:.0f}s; the Unity main "
                f"thread has not run a frame in that long. Container log "
                f"tail:\n  " + "\n  ".join(tail_container_log(15))) from exc
        rtt = time.time() - t0
        if not reply.startswith("Online"):
            raise MainThreadStalled(f"probe answered {reply[:200]!r}, not `Online N`")
        return rtt

    def players_online(self, deadline: float = 15.0) -> int:
        """Player count from the probe's own reply, so it costs no extra trip."""
        reply = self.command("players", deadline=deadline)
        found = re.search(r"Online (\d+)", reply)
        if not found:
            raise MainThreadStalled(f"cannot read player count from {reply[:200]!r}")
        return int(found.group(1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--max-print", type=int, default=600)
    ap.add_argument("--probe", action="store_true",
                    help="measure main-thread liveness and exit")
    args = ap.parse_args()

    if args.probe:
        with Rcon() as rc:
            rtt = rc.probe()
        print(f"main thread alive, probe rtt {rtt * 1000:.0f}ms")
        return 0

    cmds: list[str] = []
    if args.command:
        cmds.append(" ".join(args.command))
    if args.file:
        cmds += [ln.strip() for ln in Path(args.file).read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")]
    if not cmds:
        ap.error("nothing to do")

    with Rcon() as rc:
        for cmd in cmds:
            reply = rc.command(cmd)
            if not args.quiet:
                shown = reply if len(reply) <= args.max_print else reply[:args.max_print] + f"... [{len(reply)} bytes]"
                print(f"> {cmd}\n{shown}")
            if args.delay:
                time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
