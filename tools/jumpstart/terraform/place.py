#!/usr/bin/env python3
"""Send a `to_rcon_plan.py` blueprint plan to a live server, batched, echo-
verified, liveness-gated and resumable.  The only sender.

PLACEMENT IS SAFE TO BATCH.  VERIFICATION IS THE DANGEROUS HALF.
================================================================
This file used to say the opposite, and so did everyone who read it.  The
correction, MEASURED from the deployed ValheimRcon 1.6.2 IL (see `rcon.py` for
the instruction offsets):

  * `Log.Message("Command completed: " + command + "\\n" + result.Text)` runs
    on the FULL result text, inline on the Unity main thread, BEFORE
    `ValidatePayloadLength` truncates anything.  A big write there blocks the
    main thread on the container's stdout.
  * `InvokeConsoleCommand.OnHandle` returns exactly
    `"Command '" + joined + "' executed."` and NEVER the console command's
    output.  So a `consoleCommand` round trip logs about twice the command
    text, and this sender caps the command text at `MAX_PAYLOAD`.  A placement
    batch's logged bytes are bounded by construction and do not grow with the
    world.
  * `findObjects` is the unbounded one: its per-ZDO listing IS `result.Text`.
    Every wedge on this project followed a `findObjects` listing.

Consequence: bulk placement is not the risk.  Listing what you placed is.
`rcon.py::guard` enforces that; this sender never issues a query at all.  It
counts nothing and lists nothing -- verify out of band, once, with
`objects_count` read from the container log (`console.py`), or offline against
the plan file.

WHY BATCHED
-----------
A plan line is a World Edit Commands command, not a ValheimRcon one.
MEASURED: `spawn_object` sent as a bare RCON verb answers `Unknown command
spawn_object`; ValheimRcon's own spawn verb is `spawn` and takes positional
coordinates with no `data=`.  So plan lines go through the `consoleCommand`
bridge, and Server Devcommands' `Multiple commands per line = true` in the
deployed config means `consoleCommand a;b;c` runs all three in one trip.

Round trips are the throughput floor, not bandwidth:
`AsynchronousSocketListener` reads at most ONE packet per client per Unity
frame and `HandleCommandAsync` costs further frames dispatching to the main
thread, so MEASURED RTT is 32-35 ms idle -- 2-4 frames.  Batching ~43 lines
per trip turns 1,907 trips into ~50.

THE ECHO IS THE CONTROL
-----------------------
Every reply is compared byte-for-byte against `Command '<joined>' executed.`
A desynchronised stream is DETECTED rather than counted as a success -- the
failure mode that once reported "1,907 ok" for a run that had placed nothing.

LIVENESS
--------
`Rcon.probe()`, every `--probe-every` batches.  A reply cannot arrive until
the Unity main thread has run the queued action, so a reply PROVES main-thread
health; nothing is inferred from the log, whose heartbeat is a MEASURED 600 s
signal and therefore useless in a loop.  See `rcon.py`.

Usage:
    place.py plan.txt --checkpoint /tmp/site.ckpt
    place.py plan.txt --resume --checkpoint /tmp/site.ckpt
    place.py plan.txt --dry-run          # batching only, no server
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rcon import (  # noqa: E402
    BRIDGE,
    ECHO_OVERHEAD,
    MAX_PAYLOAD,
    MainThreadStalled,
    Rcon,
    log_sink_state,
    payload_size,
    recover_log_sink,
)

# 3600 leaves 450 bytes of headroom under the reply cap, which is more than
# three times the longest single line a plan emits (MEASURED 131 bytes), so no
# batch can be one line away from truncation.
DEFAULT_BATCH_BYTES = 3600


def plan_lines(path: Path, limit: int = 0) -> list[str]:
    lines = [ln.strip() for ln in path.read_text("utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    return lines[:limit] if limit else lines


def batches(lines: list[str], budget: int) -> list[list[str]]:
    """Group lines so `";".join(group)` stays inside `budget` bytes.

    A line longer than the budget is emitted alone: the caller cannot split a
    `spawn_object` command, and a lone over-long line is rejected loudly by
    `guard()` rather than silently truncated on the wire.
    """
    out: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        cost = payload_size(line) + (1 if current else 0)
        if current and size + cost > budget:
            out.append(current)
            current = [line]
            size = payload_size(line)
            continue
        current.append(line)
        size += cost
    if current:
        out.append(current)
    return out


def batch_budget(requested: int) -> int:
    """The joined-command budget, clamped by both directions of the cap.

    Outbound the request is `consoleCommand ` + joined; inbound the reply is
    `Command '` + joined + `' executed.`  The reply is the larger of the two,
    so it binds.
    """
    return min(requested, MAX_PAYLOAD - ECHO_OVERHEAD, MAX_PAYLOAD - len(BRIDGE))


class Aborted(RuntimeError):
    """Sending stopped deliberately; the checkpoint is the resume point."""


def send(rc: Rcon, groups: list[list[str]], *, start: int, total: int,
         ckpt: Path | None, plan: str, probe_every: int, probe_deadline: float,
         save_every: int, delay: float) -> tuple[int, list[float], list[float]]:
    """Send confirmed batches.  Returns (commands sent, batch rtts, probe rtts).

    Raises `Aborted` on an unconfirmed echo and lets `MainThreadStalled` out of
    the probe untouched -- both leave the checkpoint at the last CONFIRMED
    line, so a resume re-sends nothing that landed and skips nothing that did
    not.
    """
    sent = 0
    since_save = 0
    rtts: list[float] = []
    probes: list[float] = []
    began = time.time()

    def checkpoint() -> None:
        if ckpt:
            ckpt.write_text(json.dumps({"plan": plan, "confirmed": start + sent,
                                        "total": total}))

    for n, group in enumerate(groups, 1):
        joined = ";".join(group)
        want = f"Command '{joined}' executed."
        t0 = time.time()
        reply = rc.command(BRIDGE + joined).strip()
        rtts.append(time.time() - t0)
        if reply != want:
            checkpoint()
            raise Aborted(
                f"batch {n} NOT CONFIRMED after {sent} command(s) in "
                f"{time.time() - began:.1f}s\n"
                f"  sent  {len(joined)}B, first command: {group[0]}\n"
                f"  reply {len(reply)}B: {reply[:300]!r}")
        sent += len(group)
        since_save += len(group)
        checkpoint()

        if probe_every and n % probe_every == 0:
            probes.append(rc.probe(deadline=probe_deadline))
        if save_every and since_save >= save_every:
            rc.command("save")
            since_save = 0
        if n % 10 == 0 or n == len(groups):
            print(f"  batch {n}/{len(groups)}  {sent}/{total - start} commands "
                  f"{time.time() - began:.1f}s", flush=True)
        if delay:
            time.sleep(delay)
    return sent, rtts, probes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--batch-bytes", type=int, default=DEFAULT_BATCH_BYTES)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds between batches; 0 is fine, the server paces itself")
    ap.add_argument("--limit", type=int, default=0, help="only the first N plan lines")
    ap.add_argument("--checkpoint", help="record the last confirmed line index here")
    ap.add_argument("--resume", action="store_true",
                    help="start from the checkpoint instead of the top")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--probe-every", type=int, default=10,
                    help="probe main-thread liveness every N batches (0 disables)")
    ap.add_argument("--probe-deadline", type=float, default=15.0,
                    help="seconds a probe may take before the main thread is "
                         "declared stalled; MEASURED idle rtt is 32-35ms")
    ap.add_argument("--save-every", type=int, default=0,
                    help="issue `save` every N confirmed commands (0 disables)")
    ap.add_argument("--allow-players", action="store_true",
                    help="send even with players online; a stall freezes them")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the batching and exit without connecting")
    args = ap.parse_args(argv)

    lines = plan_lines(Path(args.plan), args.limit)
    budget = batch_budget(args.batch_bytes)

    start = 0
    ckpt = Path(args.checkpoint) if args.checkpoint else None
    if args.resume and ckpt and ckpt.exists():
        start = int(json.loads(ckpt.read_text())["confirmed"])
        print(f"resuming at line {start}/{len(lines)}")

    groups = batches(lines[start:], budget)
    if not groups:
        print(f"{args.plan}: nothing left to send")
        return 0
    sizes = [payload_size(";".join(g)) for g in groups]
    print(f"{args.plan}: {len(lines) - start} command(s) in {len(groups)} batch(es), "
          f"joined bytes min {min(sizes)} median {int(statistics.median(sizes))} "
          f"max {max(sizes)} (budget {budget})")
    if args.dry_run:
        return 0

    began = time.time()
    sink = log_sink_state()
    if sink["stalled"]:
        # MEASURED on Ulfsland: this clears in about 2 s and does not touch the
        # game, where a container restart costs 3-7 minutes.  See
        # `rcon.recover_log_sink`.
        print(f"log sink STALLED {sink['wchans']}; recovering before sending")
        sink = recover_log_sink()
        if sink["stalled"]:
            print("log sink is still stalled; REFUSING to send. Sustained "
                  "console output into a stalled sink fills the game's own "
                  f"stdout pipe and then blocks it.\n{sink['raw']}",
                  file=sys.stderr)
            return 6
        print(f"log sink recovered {sink['wchans']}")
    with Rcon(timeout=args.timeout) as rc:
        preflight = rc.probe(deadline=args.probe_deadline)
        online = rc.players_online(deadline=args.probe_deadline)
        print(f"preflight: main thread alive, rtt {preflight * 1000:.0f}ms, "
              f"{online} player(s) online, log sink {sink['wchans']}")
        if online and not args.allow_players:
            print("REFUSING to send with players online: a main-thread stall "
                  "freezes them in game and only a container restart recovers. "
                  "Pass --allow-players to override.", file=sys.stderr)
            return 3
        try:
            sent, rtts, probes = send(
                rc, groups, start=start, total=len(lines), ckpt=ckpt,
                plan=args.plan, probe_every=args.probe_every,
                probe_deadline=args.probe_deadline, save_every=args.save_every,
                delay=args.delay)
        except Aborted as exc:
            print(f"\n{exc}", file=sys.stderr)
            if ckpt:
                print(f"  resume with: place.py {args.plan} --resume "
                      f"--checkpoint {ckpt}", file=sys.stderr)
            return 4
        except MainThreadStalled as exc:
            # A stalled main thread may be a CONSEQUENCE of a stalled log sink:
            # supervisord reads the game's stdout pipe, so while it is stuck in
            # its own blocked write it drains nothing and the game's next
            # console write blocks.  So ask the sink before declaring the game
            # lost, and if that is the cause, clear it and finish the run
            # instead of leaving a half-built structure for a restart.
            print(f"\nMAIN THREAD STALLED\n{exc}", file=sys.stderr)
            recovered = recover_log_sink()
            if recovered["stalled"]:
                print(f"log sink also stalled and did NOT recover: "
                      f"{recovered['raw']}", file=sys.stderr)
                if ckpt:
                    print(f"  resume with: place.py {args.plan} --resume "
                          f"--checkpoint {ckpt}", file=sys.stderr)
                return 5
            print(f"log sink cleared ({recovered['wchans']}); re-probing",
                  file=sys.stderr)
            rc.close()
            try:
                rc.probe(deadline=args.probe_deadline)
            except MainThreadStalled as again:
                print(f"main thread still stalled after clearing the sink; "
                      f"this is not the sink.\n{again}", file=sys.stderr)
                if ckpt:
                    print(f"  resume with: place.py {args.plan} --resume "
                          f"--checkpoint {ckpt}", file=sys.stderr)
                return 5
            done = int(json.loads(ckpt.read_text())["confirmed"]) if ckpt else start
            print(f"resuming at line {done}/{len(lines)} after sink recovery",
                  file=sys.stderr)
            sent, rtts, probes = send(
                rc, batches(lines[done:], budget), start=done,
                total=len(lines), ckpt=ckpt, plan=args.plan,
                probe_every=args.probe_every,
                probe_deadline=args.probe_deadline,
                save_every=args.save_every, delay=args.delay)
            sent += done - start
        after = rc.probe(deadline=args.probe_deadline)

    total = time.time() - began
    rate = sent / total if total else 0.0
    print(f"confirmed {sent}/{len(lines) - start} command(s) in {total:.1f}s "
          f"over {len(groups)} round trip(s), {rate:.0f} pieces/s; "
          f"batch rtt min {min(rtts) * 1000:.0f}ms "
          f"median {statistics.median(rtts) * 1000:.0f}ms "
          f"max {max(rtts) * 1000:.0f}ms")
    print(f"liveness: preflight {preflight * 1000:.0f}ms, "
          f"{len(probes)} in-flight probe(s)"
          + (f" max {max(probes) * 1000:.0f}ms" if probes else "")
          + f", post-run {after * 1000:.0f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
