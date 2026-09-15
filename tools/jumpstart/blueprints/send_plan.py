#!/usr/bin/env python3
"""Send a `to_rcon_plan.py` plan to a live server over RCON, batched.

WHY THIS IS NOT `terraform/place.py`
------------------------------------
A plan line is a **World Edit Commands** command, not a ValheimRcon one.
MEASURED on Ulfsland tonight, against the deployed ValheimRcon 1.6.2:

    'spawn_object'  -> 'Unknown command spawn_object'

ValheimRcon's own spawn command is called `spawn` and takes positional
`<prefab> <x> <y> <z>`; `spawn_object` with `pos=`/`rot=`/`from=`/`data=` lives
in `WorldEditCommands.dll` and is only reachable through ValheimRcon's
`consoleCommand` bridge. `place.py` sends the line bare and greps the reply for
`Spawned N objects:`, which is ValheimRcon's `spawn` wording -- so against this
plan it can only ever report failures. This sender uses the bridge.

WHY BATCHED, AND WHY THAT IS THE FIX FOR THE WEDGE
--------------------------------------------------
Server Devcommands has `Multiple commands per line = true` in the deployed
`server_devcommands.cfg`, and it patches the console's own command entry, so a
single `consoleCommand a;b;c` runs all three. MEASURED: three `spawn_object`
commands in one 239-byte round trip, 35 ms, all three ZDOs created at the
requested positions and read back with `findObjects -prefab`.

That turns 1,907 round trips into ~50. It matters because the wedge this
replaces was a function of ROUND TRIP COUNT, not of reply size:

  * MEASURED: a `consoleCommand spawn_object ...` reply is
    `Command '<the command>' executed.` -- 94 bytes for a bare floor piece,
    and bounded by the command's own length (131 bytes is the longest line
    this plan emits). The per-object `-Prefab: ... Id: ... Position: ...` echo
    that motivated one-command-per-trip goes to the server CONSOLE, which
    surfaces in the container log, NOT into the RCON reply. So reply
    truncation was never in play on this path.
  * MEASURED from ValheimRcon's source at the deployed version: a reply that
    WOULD exceed the 4050-byte payload cap is truncated by
    `RconCommandReceiver.ValidatePayloadLength` before the packet is built, so
    an over-long reply arrives as a well-formed short packet ending in
    `--- message truncated ---`. A truncated reply is therefore not a
    malformed frame either.
  * `AsynchronousSocketListener.Update` reads AT MOST ONE packet per client per
    Unity frame, and `RconProxy.HandleCommandAsync` hands the work to a
    separate `MainThreadDispatcher` MonoBehaviour and awaits it, which costs
    further frames. MEASURED RTT floor on an idle Ulfsland: 30-65 ms, i.e.
    2-4 frames. 1,907 sequential trips cannot go faster than about a minute and
    spend that whole minute holding one connection open through a
    single-packet-per-frame reader. ~50 trips spend two seconds.

`RconPeer.TryReceive` is also unframed: it reads however many bytes are
available into one 4096-byte buffer, parses exactly ONE packet out of it and
then CLEARS the buffer, so a second queued packet is discarded and a packet
split across two TCP segments is parsed against a zero-padded tail. Neither can
happen while exactly one command is in flight, which is why this sender stays
strictly synchronous and verifies the echo rather than pacing and hoping.

THE ECHO IS THE CONTROL
-----------------------
`consoleCommand` answers with the batch text quoted back verbatim. Every reply
is compared against what was sent, so a desynchronised stream is DETECTED
rather than counted as a success -- the failure mode that reported "1,907 ok"
for a run that had not placed 1,907 pieces. A mismatch stops the send.

Placement is resumable: `--checkpoint` records the last confirmed line index,
so a stall costs the remaining batches and not the run.

Usage:
    send_plan.py plan.txt --checkpoint /tmp/iron-era.ckpt
    send_plan.py plan.txt --resume --checkpoint /tmp/iron-era.ckpt
    send_plan.py plan.txt --dry-run          # batch sizes only, no server
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERRAFORM = HERE.parent / "terraform"
sys.path.insert(0, str(TERRAFORM))

BRIDGE = "consoleCommand "

# ValheimRcon's `RconPacket.MaxPayloadSize`. Both directions: an inbound packet
# over this is rejected by the parser, and an outbound one is truncated.
MAX_PAYLOAD = 4050

# The bridge answers `Command '<joined>' executed.`, so the REPLY is 22 bytes
# longer than the joined command text and is the binding constraint.
ECHO_OVERHEAD = len("Command '' executed.") + 2

# Default joined-command budget. 3600 leaves 450 bytes of headroom under the
# reply cap, which is more than three times the longest single line this plan
# emits (131 bytes), so no batch can be one line away from truncation.
DEFAULT_BATCH_BYTES = 3600


def batches(lines: list[str], budget: int) -> list[list[str]]:
    """Group lines so that `;`.join(group) stays inside `budget` bytes.

    A single line longer than the budget is emitted alone rather than split:
    splitting a command is how you place half a building.
    """
    out: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        cost = len(line.encode("utf-8")) + (1 if current else 0)
        if current and size + cost > budget:
            out.append(current)
            current, size = [], 0
            cost = len(line.encode("utf-8"))
        current.append(line)
        size += cost
    if current:
        out.append(current)
    return out


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
    ap.add_argument("--dry-run", action="store_true",
                    help="print the batching and exit without connecting")
    args = ap.parse_args(argv)

    lines = [ln.strip() for ln in Path(args.plan).read_text("utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    if args.limit:
        lines = lines[:args.limit]

    budget = min(args.batch_bytes, MAX_PAYLOAD - ECHO_OVERHEAD,
                 MAX_PAYLOAD - len(BRIDGE))
    start = 0
    ckpt = Path(args.checkpoint) if args.checkpoint else None
    if args.resume and ckpt and ckpt.exists():
        start = int(json.loads(ckpt.read_text())["confirmed"])
        print(f"resuming at line {start}/{len(lines)}")

    groups = batches(lines[start:], budget)
    sizes = [len(";".join(g).encode("utf-8")) for g in groups]
    print(f"{args.plan}: {len(lines) - start} command(s) in {len(groups)} batch(es), "
          f"joined bytes min {min(sizes)} median {int(statistics.median(sizes))} "
          f"max {max(sizes)} (budget {budget})")
    if args.dry_run:
        return 0

    from rcon import Rcon  # noqa: E402  (import late so --dry-run needs no server)

    sent = 0
    rtts: list[float] = []
    began = time.time()
    with Rcon(timeout=args.timeout) as rc:
        for n, group in enumerate(groups, 1):
            joined = ";".join(group)
            want = f"Command '{joined}' executed."
            t0 = time.time()
            reply = rc.command(BRIDGE + joined).strip()
            rtts.append(time.time() - t0)
            if reply != want:
                print(f"\nBATCH {n} NOT CONFIRMED after {sent} command(s) in "
                      f"{time.time() - began:.1f}s", file=sys.stderr)
                print(f"  sent  {len(joined)}B, first command: {group[0]}", file=sys.stderr)
                print(f"  reply {len(reply)}B: {reply[:300]!r}", file=sys.stderr)
                return 4
            sent += len(group)
            if ckpt:
                ckpt.write_text(json.dumps({"plan": args.plan,
                                            "confirmed": start + sent,
                                            "total": len(lines)}))
            if n % 10 == 0 or n == len(groups):
                print(f"  batch {n}/{len(groups)}  {sent}/{len(lines) - start} commands "
                      f"{time.time() - began:.1f}s", flush=True)
            if args.delay:
                time.sleep(args.delay)

    total = time.time() - began
    print(f"confirmed {sent}/{len(lines) - start} command(s) in {total:.1f}s "
          f"over {len(groups)} round trip(s); rtt min {min(rtts) * 1000:.0f}ms "
          f"median {statistics.median(rtts) * 1000:.0f}ms max {max(rtts) * 1000:.0f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
