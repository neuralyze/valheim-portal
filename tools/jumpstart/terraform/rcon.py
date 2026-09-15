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
        if "-prefab" not in low or "-near" not in low:
            raise UnboundedQuery(
                "findObjects must be BOTH prefab-scoped and radius-bounded: "
                "every filter in its usage string is optional, so the "
                "unscoped form lists the entire world into one main-thread "
                "log write. Use: findObjects -near <x> <y> <z> <radius> "
                "-prefab <prefab>")
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
target=$(readlink /proc/$sys/fd/1 2>/dev/null)
for f in /proc/$sup/fd/*; do
    if [ "$(readlink "$f" 2>/dev/null)" = "$target" ]; then echo "READFD $f"; fi
done
"""

# `unix_wait_for_peer` on supervisord together with `pipe_write` on syslogd is
# the signature.  Either alone is a normal momentary state.
SINK_STALL_WCHANS = ("unix_wait_for_peer", "pipe_write")


def _in_container(script: str, name: str = CONTAINER, timeout: float = 30.0) -> str:
    out = subprocess.run(["sudo", "-n", "docker", "exec", name, "bash", "-c", script],
                         capture_output=True, text=True, timeout=timeout)
    return out.stdout + out.stderr


def log_sink_state(name: str = CONTAINER) -> dict:
    """Whether the container's stdout chain is moving, and where it is stuck.

    Returns `{"stalled": bool, "wchans": (sup, sys), "read_fds": [...]}`.
    Processes are found by name, never by the pids observed once: they differ
    per container and per restart.
    """
    text = _in_container(_SINK_PROBE_SH, name)
    wchans: tuple[str, ...] = ()
    read_fds: list[str] = []
    for line in text.splitlines():
        if line.startswith("WCHAN "):
            wchans = tuple(line.split()[1:])
        elif line.startswith("READFD "):
            read_fds.append(line.split(None, 1)[1].strip())
    stalled = len(wchans) == 2 and tuple(wchans) == SINK_STALL_WCHANS
    return {"stalled": stalled, "wchans": wchans, "read_fds": read_fds,
            "raw": text.strip()}


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

    Returns the state AFTER the attempt; check `["stalled"]`.
    """
    before = log_sink_state(name)
    if not before["stalled"]:
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
