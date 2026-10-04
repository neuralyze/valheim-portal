# Build harness and protocol: driving a live Valheim server without losing work

Written 2026-09-18 for an agent with no memory of the session that produced it. Every
rule below carries the measurement that produced it and the defect it prevents. Where a
technique was tried and failed it is recorded as a **REFUSAL** with its numbers, because
several of those cost hours.

Two labels are used throughout and they are not decoration:

- **MEASURED** — a number an instrument returned, with the instrument named.
- **INFERRED** — a conclusion drawn from measurements, marked so a later session can
  attack it.

And two more, which are the spine of the whole exercise:

- **SEED-DERIVED** — recomputable from the world seed and the game/mod build. Survives a
  world regeneration.
- **SAVE-DERIVED** — exists only in this save's ZDO set / `.db`. Does **not** survive a
  regeneration, and in several cases does not survive a *restart*.

Scope: this file is the transport, the protocol and the replay contract. Terrain, water
and structure geometry are separate manuals in this directory.

---

## 0. The instruments, by name

Everything in this document is reachable through five files. Do not write a sixth
transport.

| Instrument | Path | What it is |
|---|---|---|
| `terraform/rcon.py` | `tools/jumpstart/terraform/rcon.py` (840 lines) | The only socket client. Source-protocol RCON, the unbounded-query `guard()`, the main-thread `probe()`, and the host-side sink diagnosis/repair. |
| `terraform/console.py` | `tools/jumpstart/terraform/console.py` | Reads a console command's *output* back out of the container log, sliced on the command's own echo. |
| `terraform/place.py` | `tools/jumpstart/terraform/place.py` | The batched, echo-verified, resumable plan sender. |
| `ledger/live.py` | `tools/jumpstart/ledger/live.py` | `LiveBuilder` — the only sanctioned way to write to the live world. |
| `ledger/replay.py` | `tools/jumpstart/ledger/replay.py` (1,280 lines) | `Server` (transport + `count`), `send_wire`, `verify_expect`. Build and replay share them by construction. |

**Which PACKAGE owns which VERB is a different question, and it has its own table in
§16.1.** Read it before planning any operation: three subagents and the orchestrator
each got it wrong in one night, and the mistake deleted a terrain compiler that is
still gone (§16.3).

---

## 1. RCON: reaching the server at all

### 1.1 The port is not published to the host

Connect to the **container bridge IP** on **2458**. `127.0.0.1:2458` is refused, and
**that refusal is a mapping fact, not a dead server.**

- MEASURED, recorded in the build brief 2026-09-14: "standard Source-RCON on port 2458,
  reachable ONLY on the container bridge IP (not published to the host)".
- The IP **changes on every stop/start** because hostops recreates the network — it was
  `192.168.32.2` at one point on 2026-09-15 and something else hours later. So it is
  resolved fresh, never cached:
  `rcon.py::container_ip()` runs
  `docker inspect <container> --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'`
  and raises if it is empty rather than returning a default.
- Credentials come from the world's own config, never from a constant:
  `rcon.py::rcon_credentials()` parses `Port` and `Password` out of
  `$VALHEIM_ROOT/<World>/config_merged/bepinex/org.tristan.rcon.cfg`.

REFUSAL: hand-written probes that used `127.0.0.1:2458` appear in the session record
(2026-09-15, `clear.py --rcon "127.0.0.1:2458:$P"`). They work only where something else
has already forwarded the port. Do not copy that form.

The world is a **parameter with an Ulfsland default**, not a constant. `rcon.py` reads
`VALHEIM_WORLD` / `VALHEIM_CONTAINER` / `VALHEIM_RCON_CONFIG` / `VALHEIM_WORLD_ROOT`.
This was changed for a reason recorded in the file: a fresh same-seed world could be
provisioned and started and **nothing in `terraform/` or `ledger/` could address it**,
because `replay.py::restart_and_wait` imported the container name. A rebuild is exactly
the case where the target is not Ulfsland.

### 1.2 Every game command goes through the `consoleCommand` bridge

`ValheimRcon`'s verb registry is **closed over its own assembly**, with no console
fallback. A bare verb it does not own answers `Unknown command <verb>`.

MEASURED, 2026-09-17:

```
ValheimRcon 1.6.2 registers 41 verbs of its own.
The server console surface has 443 registered commands.
`save` and `players` work as bare verbs ONLY because they are 2 of the 41.
`spawn_object`, `objects_count`, `deleteObjects`, `findObjects`, `terrain`
  are console commands and MUST be sent as `consoleCommand <cmd>`.
```

In code: `rcon.py::BRIDGE = "consoleCommand "`, and `Rcon.console(cmd)` is
`self.command(BRIDGE + cmd)`.

**The cost of forgetting this: one hour, and a false alarm on a healthy world.**
2026-09-17T14:03 — an agent hand-wrote RCON probes without the bridge, read the resulting
`Unknown command` as a live-world regression, spent an hour diagnosing it, and reported
the world as broken to the operator. The command surface was fully alive the whole time.
The trap was **already recorded in two source comments** — `terraform/place.py:33` and
`blueprints/to_rcon_plan.py:65`, both stating that `spawn_object` sent as a bare RCON verb
answers `Unknown command spawn_object`. Every tool in the repo sends the bridge; only the
hand-written probe did not. It also proved the premise could not hold before touching
anything: `ValheimRcon.dll` was byte-identical (md5 `aa0fa93e…`) across the pre-deploy
backup, the host staging tree and the live in-container tree.

**The instrument was the defect and the subject was healthy.** Assume that first.

### 1.3 The client's 4096-byte buffer is the binding limit, not the server's 4050

MEASURED 2026-09-16 from `RconPeer.TryReceive`: it reads whatever is available into **one
4096-byte buffer**, parses exactly **one** packet, and then **clears the buffer**.

Consequences, all measured:

- An oversized reply is parsed against a zero-padded tail and **the stream
  desynchronises**. The reply that first broke it was only ~4.4 KB / 39 rows — the
  *server* ran that query fine.
- A desync **poisons the peer for later connections**: one agent's run died inside
  `Rcon.connect()` at `_recv` **before it sent a single command**, having inherited
  another agent's desynced stream, and spent its whole window diagnosing a fault that was
  not its own. Curable only by a container restart.
- One agent poisoned a peer personally with an unbounded 40 m `findObjects` listing for
  **448 pieces** — the exact hazard that had been broadcast to every peer an hour
  earlier.
- **Strictly one command in flight.** `Rcon.command` is synchronous for this reason:
  pipelining silently desynchronises the stream, because the second queued reply is
  discarded by that buffer clear.
- Keep positional replies **under half the client buffer**.

Server-side cap for completeness: `RconPacket.MaxPayloadSize` is **4050** bytes
(`ValidatePayloadLength`, IL_0006 `ldc.i4 4050`) — inbound over that is rejected, outbound
is truncated. `rcon.py::MAX_PAYLOAD = 4050` and `guard()` refuses an over-long request
before a byte reaches the socket.

### 1.4 `guard()` — what the transport refuses, and why each refusal is narrow

`rcon.py::guard(cmd)` runs on **every** `command()` call, on the native verb only.

- `findObjects` **must** be radius-bounded. Every filter in its usage string is optional,
  so the bare verb lists the whole world. Usage, MEASURED from the plugin:
  `findObjects -near <x> <y> <z> <radius> -zone <x> <y> -prefab <prefab> -creator <id> -id <id:userid> -tag <tag> -tag-old <tag> -detailed`
- An **unscoped** `findObjects -near` is capped at `UNSCOPED_NEAR_MAX_M = 8.0` m of
  half-extent. The bound is stated in the units of the hazard: MEASURED over two road
  corridors the densest 32 m cell holds **334 objects** (~0.33 objects/m²), and a row is
  ~62 bytes (MEASURED: 24 rows = 1,404 B, 47 rows = 3,040 B) — so a 256 m² unscoped box is
  ~85 objects and ~5 KB, two orders below the outage. Half-extent 20 m would be ~33 KB and
  is refused.
- `logs -lines N` is capped at `LOG_LINES_CAP = 200`. Bare `logs` defaults to **5**
  (IL_0028 `ldc.i4.5`) and is harmless.
- `globalKeys`, `adminlist`, `banlist`, `permitted`, `players`, `showContainer` are **not**
  guarded — they are bounded by things that are not the world size, and guarding them
  would have broken `jumpstart.py`'s `globalKeys` read for no measured reason.

REFUSAL worth reading before you widen the guard: treating "has a `-prefab` filter" as the
rule rather than as a *proxy* for boundedness cost this project its cheapest census. The
corridor then had to be censused by **name, 139 names per region**, and the name list had
to come from `objects_count`, whose table goes through the console sink — the subsystem
that failed four times in one session and takes a 4–7 minute container restart with it.

---

## 2. Placement is safe to batch. Verification is the dangerous half.

This is the opposite of what the project believed for most of one night, and the wrong
version costs an outage. MEASURED by disassembling the **deployed** `ValheimRcon.dll`
1.6.2 with `monodis`, references resolved from the container's own `BepInEx/core` and
`valheim_server_Data`:

```
RconProxy.HandleCommandAsync
  queues EVERY command through ThreadingUtil.RunInMainThread and awaits the
  TaskCompletionSource (IL_0073, IL_0083-IL_008d) before it can return anything.
  On resume, IL_00e1-IL_0101:
      Log.Message("Command completed: " + command + "\n" + result.Text)
  on the FULL, untruncated text. The TaskCompletionSource has no
  RunContinuationsAsynchronously, so that continuation runs INLINE on the Unity
  MAIN thread. The write goes to container stdout via BepInEx + supervisord.
  ValidatePayloadLength truncates at 4050 -- but LATER, when the reply packet is
  built. Truncation never protects the log.
```

So the wedge is a function of exactly one thing: **how many bytes land in
`CommandResult.Text`.** That splits the command surface cleanly:

- `InvokeConsoleCommand.OnHandle` (IL_001f-IL_003c) calls `Terminal.TryRunCommand` and
  returns exactly `"Command '" + joined + "' executed."` — it **never** carries the
  console command's output. A `consoleCommand` round trip therefore logs about twice the
  command text, and the command text is capped at `MAX_PAYLOAD`. **A placement batch's
  logged bytes cannot grow with world size.** This is why batched placement is safe.
- `findObjects` is the opposite: its per-ZDO listing **is** `result.Text`. ~1,900 pieces is
  **~200 KB in a single main-thread write**. That is the outage. MEASURED twice, each
  costing a container restart: `findObjects -detailed` over a cube containing all 1,907
  pieces wedged the server and the container log stopped mid-object at id 80439; and a
  tiled sweep of ~700 small `findObjects` cubes wedged it the same way after **1,082
  object lines in about ten seconds**.

Therefore: **never ask a question whose ANSWER is long, and keep the total answered volume
small.** `blueprints/verify_placement.py` enforces `DEFAULT_BUDGET = 400` object lines per
run plus a sustained rate cap, so that file cannot repeat the failure it documents.

### 2.1 Batch sizes, with the arithmetic

MEASURED constants:

| Constant | Value | Where it comes from |
|---|---|---|
| `deleteObjects` / console echo per object | **~155 B** | MEASURED 2026-09-16; it is why a retire radius is capped at 20 m |
| Container stdout pipe | **65,536 B** | the kernel pipe the game writes into |
| RCON client reply buffer | **4,096 B** | `RconPeer.TryReceive` |
| Server payload cap | **4,050 B** | `ValidatePayloadLength` IL_0006 |
| `players` reply | **8 B**, RTT **32–35 ms** idle, n=12, min 32 max 35 | `rcon.py::probe` |
| Round-trip cost | **2–4 Unity frames** — one packet per client per frame | `AsynchronousSocketListener` |

In code, `settlements/harbour_city.py:1567`:

```python
# 48 pieces per record, not 64.  MEASURED reason: waterfront's 64-piece budget
# sits at ~9,920 B against a 65,536 B pipe, and a 1,255-piece record with no
# gate anywhere inside it wedged the log sink and left a structure at 272 of
# 1,255 pieces which then had to be RETIRED AND REBUILT.  48 keeps a record's
# echo near a third of the pipe and leaves room for the longer `fb_` prefab
# names, which are up to 12 characters longer than `wood_floor`.
BATCH_PIECES = 48
BATCH_BYTES = 1800
```

So: **48–64 pieces per ledger record** (~9,920 B, ~15% of the 65,536 B pipe), **~1,800 B
per round trip** (~45% of the 4,096 B client buffer), with a **host-side sink check
between records**. `chunk_rows()` splits on whichever limit is hit first.

Throughput for scale: Server Devcommands ships `Multiple commands per line = true`, so
`consoleCommand a;b;c` runs all three in one trip. Batching ~43 lines per trip turned
1,907 trips into ~50, and a 1,907-piece body lands in 45 round trips and **1.8 s** rather
than 1,907 trips and a minute. At one command per trip the same path costs **612 round
trips for one house and 19,203 for the world**.

### 2.2 The echo is the control

`replay.py::Server.console_echo` compares the reply **byte-for-byte** against
`Command '<cmd>' executed.` and raises on mismatch, saying explicitly that nothing is
assumed about the world. **It is a TRANSPORT control and nothing more**: it proves the
bridge accepted the string and the stream is still in sync, never that the command did
anything — the same receipt comes back for a verb that is not installed at all (§3.5).
The world is confirmed by the record's `expect` postcondition or by a census.

REFUSAL: the earlier transport confirmed a batch by grepping the **container log** for the
command's echo. MEASURED by an agent mid-build: container log lines are **truncated at
~150 characters** — a `Command completed: consoleCommand` line was cut mid-word at **153**
— while a batch is up to **~3,600 characters**. So its echo can never appear intact,
`console.py::slice_for` raises `OutputNotFound`, and the run aborts **after the pieces have
landed**. A successful placement read as a failure, and a half-built building was left
standing. The socket reply is not subject to the log cap and a byte-for-byte comparison is
strictly stronger than a log grep, because a mismatch also detects a desynchronised
stream.

---

## 3. The sinks, the wedge, and the exact recovery

There are **four** distinct failure modes in this chain and two of them have been
misdiagnosed as a third. Check in this order; each signature is independent evidence.

| # | Symptom | Cause | Remedy |
|---|---|---|---|
| 1 | RCON does not answer at all (refused / timeout) | **The wedge**: `Log.Message` writing an unbounded `CommandResult.Text` on the Unity main thread, or the game's stdout pipe backed up | Drain from the host (§3.2). Never SIGKILL. |
| 2 | RCON answers, log file **still growing**, console read returns nothing | **Clock race** — a `--since` cursor against a log supervisord stamps at 1 s resolution | None needed; slice on the echo, not a clock |
| 3 | RCON answers **fast** (MEASURED 88 ms), `status.json` updating, container stdout **frozen** | **Dead console sink** — circular wait between supervisord and syslogd | Drain the pipe, ~2 s, game untouched |
| 4 | `docker logs` returns nothing, log file stops growing, supervisord and syslogd both in normal event loops | Break is **downstream**, in containerd-shim (`futex_wait_queue`) | Not reachable from this project; read the container's own file |

### 3.1 The circular wait, measured

MEASURED on Ulfsland at 22:57:35 UTC, 2026-09-15 — container stdout stopped entirely for
six minutes while RCON round trips stayed at 88 ms:

```
pid 29   supervisord      state S    wchan unix_wait_for_peer
pid 30   syslogd          state S    wchan pipe_write
pid 170  valheim_server   state Ssl, NOT blocked
```

From `/proc/net/unix`: syslogd's fd 0 is the `/dev/log` SOCK_DGRAM socket, supervisord
holds a DGRAM socket to it, and pipe `15901491` is syslogd's stdout whose **only** reader
is supervisord's fd 7. Supervisord blocks in `sendto()` to `/dev/log` because syslogd's
datagram receive queue is full; syslogd blocks writing its stdout to the pipe only
supervisord drains, which supervisord cannot drain while stuck in the `sendto`. **Neither
side has a timeout, so it never clears itself.**

The trigger, MEASURED by draining 8,084 bytes out of that pipe and reading them: **one**
`objects_count id=* ignore=_*` over a freshly generated Plains zone, whose table began
`Total: 6149` and continued with **one line per prefab** (`rug_fur: 135`,
`Rock_4_plains: 6`, `sign: 55`, …). Hundreds of separate syslog datagrams in one burst
fill the queue and close the cycle.

This is **not** the `guard()` wedge. `objects_count` writes its table to the **console
sink**, which never touches `CommandResult.Text` at all, so it is invisible to both
`guard()` and `probe()`. INFERENCE (stated as such in the source): because supervisord is
the reader of the game's own stdout pipe, sustained console output while it is stuck fills
those pipes and then blocks the writer — which is how a sink stall becomes a main-thread
wedge. That is why it is detected and cleared rather than waited out.

The same shape recurred during a 1,255-piece placement on 2026-09-17:

```
pid 120208 (valheim_server) in pipe_write, main thread not answering RCON
supervisord pid 113676 held the read end on fd 21
```

### 3.2 THE RECOVERY PROCEDURE — from the HOST, exactly

**Do this. Do not SIGKILL. Do not restart to clear it.**

```bash
# 1. Host pids of the container's supervisord and syslogd.
#    `docker top` reads the HOST pid namespace, so these index the host's /proc
#    and need no `docker exec` into a container that may be wedged.
sudo -n docker top valheim-server-Ulfsland -eo pid,comm

# 2. The signature is the PAIR, never either half alone.
cat /proc/<supervisord-pid>/wchan     # expect: unix_wait_for_peer
cat /proc/<syslogd-pid>/wchan         # expect: pipe_write

# 3. Find supervisord's READ END of syslogd's stdout, matched by PIPE INODE --
#    the fd NUMBER is not stable across restarts.
readlink /proc/<syslogd-pid>/fd/1     # e.g. pipe:[15901491]
for f in /proc/<supervisord-pid>/fd/*; do
  [ "$(readlink "$f")" = "pipe:[15901491]" ] && echo "$f"
done

# 4. RELEASE IT: a SUSTAINED read of that read end.
timeout 75 cat /proc/<supervisord-pid>/fd/<read-end> >/dev/null

# 5. Prove it released, by wchan and by an RCON round trip.
cat /proc/<supervisord-pid>/wchan     # expect: do_poll.constprop.0
cat /proc/<syslogd-pid>/wchan         # expect: __skb_wait_for_more_packets
python3 tools/jumpstart/terraform/rcon.py --probe
```

In code this is `rcon.py::host_sink_state()` (diagnosis) and
`rcon.py::drain_log_sink_host()` (repair, which polls supervisord's wchan *underneath* the
read and stops as soon as it moves). `rcon.py::keep_sink_clear(note=...)` is the loop's
safety valve: it measures, drains if drainable, and **raises only for the shape a drain
cannot fix**. `settlements/waterfront.py::sink_gate()` is the wrapper a build loop calls
between records.

Measured recovery times, all on Ulfsland, so you know what "working" looks like:

- **~1.2 s** for the host `cat` path (recorded in the 2026-09-18 build brief, proven
  twice).
- **under 2 s** of continuous reading before supervisord returned to
  `do_poll.constprop.0` and syslogd to `__skb_wait_for_more_packets`, 2026-09-16.
- **20 s** for the older in-container `dd if=/proc/<sup>/fd/<n> of=/dev/null bs=65536`
  path, 2026-09-16T04:13 — on that occasion `docker restart` had already **TIMED OUT AT
  180 s** because the process could not be signalled; the drain released it, the queued
  SIGTERM then landed, and RCON rebound **153 s** after boot.
- After the 2026-09-17 drain, `120208` moved to `hrtimer_nanosleep` and RCON answered in
  **34 ms** again. No SIGKILL, no restart.
- **2026-09-19, the procedure run a third time by an agent who had never run it**, which
  is the test that matters for a written procedure. `players` — an 8-byte reply with a
  32–35 ms baseline — timed out at **40 s**. Signature, read per pid:
  `pid 30 syslogd wchan pipe_write, fd 1 -> pipe:[115008736]`;
  `pid 29 supervisord wchan unix_wait_for_peer, fd 7 -> the same pipe inode`. Host
  drain `sudo timeout 75 cat /proc/3170147/fd/7 >/dev/null` (exit **124** = it ran the
  whole window, which is correct, not a failure); afterwards syslogd sat in
  `skb_wait_for_more_packet`, supervisord in `do_poll`, and `players` answered
  instantly. No SIGKILL, no restart, nothing lost.

**Precondition, learned in that same pass: prove the container is NOT restarting before
you start.** Uptime 10 h and `restarts=0` is what made "wedge" the right diagnosis
rather than "deploy". Running wedge recovery against a server that is coming back up is
its own incident, and a restarting server also answers nothing.

**THE TEARDOWN-BLOCKED VARIANT, MEASURED 2026-09-19 on the fourth occurrence.** The
sink wedged a third time in one night, and this time it was blocking a container
**teardown** rather than a query — a `docker compose down` that would not finish.
**The documented host-side drain was unavailable**: the namespace mapping was already
gone, so scanning the host for the container's pids by `NSpid` found nothing to drain.
The recovery that worked was the same sustained read reached the other way:

```bash
sudo -n docker exec <container> timeout 60 cat /proc/29/fd/7 >/dev/null   # exit 124 = correct
```

That teardown then completed, `rc=0` at **240.9 s**. Note what this does and does not
contradict: §3.3 refuses the in-container **diagnostic**, because it reports a verdict
through the thing that is blocked. A `docker exec` **drain** is a different operation —
it needs no verdict, and during a teardown it is the only door left. Prefer the host
path; fall back to this one when the host cannot see the pids.

A one-shot `dd` is **not** enough — the pipe refills while syslogd catches up. The read
must be sustained.

**The price, stated because it matters:** the bytes drained are supervisord's copy of
syslogd's stdout, i.e. container log lines `docker logs` would otherwise have carried. A
drain during a `consoleCommand` round trip can eat that command's **output**, which is
what `console.py` reads. So the drain is free for a socket-only census (the echoes are pure
cost) and anything reading console output must **re-ask after a drain** rather than trust
the gap.

### 3.3 REFUSAL: the in-container diagnostic cannot see this

`rcon.py::log_sink_state()` reaches the container through `docker exec` — **exactly the
call that blocks while the sink is stalled.** MEASURED twice in one night: with supervisord
blocked in `unix_wait_for_peer`, the exec-based probe reported `fds_lost`, whose named
remedy is a 4–7 minute container restart, while the host-side read of the *same two*
`/proc` entries showed the **drainable** circular wait that a two-second read fixed with
the game untouched. **A diagnosis that costs a restart when the repair costs two seconds is
the expensive kind of wrong.** Use the host path in a loop.

**And the same instrument fails a second way, which is worse because the server
stays healthy throughout.** MEASURED: **`docker logs` for this container FROZE
at 2026-09-20T01:55:56Z, mid-`findObjects` on `wood_pole2`, and stayed frozen
for 26 hours** while the game ran and RCON answered. `rcon.log_sink_state()`
**reads that path**, so for 26 hours it reported the state of a stream that had
stopped rather than the state of the sink.

```
DEAD PATH   docker logs <container>        frozen 26 h, no error, no EOF
LIVE PATH   /var/log/supervisor/valheim-server-stdout---supervisor-*.log
              the supervisor FILE sink, inside the container -- still growing
DIAGNOSIS   host_sink_state(), which reads /proc from the HOST
```

So: read console output from the supervisor file sink (§3.5), and decide sink
state with `host_sink_state()`. **An instrument whose input can freeze without
erroring reports a frozen world**, and this one did so beside a server that was
answering every command.

Two further defects of that instrument are recorded in its own docstring and are worth
inheriting rather than rediscovering:

1. The verdict used to be a **boolean**. On the third outage `stalled` answered `False` and
   was *right about the question it asks* — "is this the circular wait" — while being
   *read* as "is the sink healthy". Every `objects_count` in the project was blind,
   `recover_log_sink` no-opped and returned that same state, and **a no-op indistinguishable
   from a repair is how the next agent spends an hour.** It is now a four-way verdict:
   `healthy` / `circular_wait` / `fds_lost` / `blocked_unknown`, each naming its own remedy,
   and `recover_log_sink` **raises** on `fds_lost` rather than no-opping.
2. The first fix then made the same mistake one level up: it decided `fds_lost` from
   `readlink /proc/<syslogd>/fd/{0,1,2}` being empty. MEASURED on a freshly restarted,
   demonstrably healthy container — 485 lines in a 120 s window, supervisord in
   `do_poll.constprop.0` — **those readlinks still read empty**, because resolving another
   process's fd links from a `docker exec` context is not permitted. An empty readlink was
   never evidence; it was a corroborating detail in the one broken case that got promoted
   to the test, and it would have sent the next agent into a needless restart. The fds are
   still reported as evidence and they decide nothing.

The only sound *active* test is `rcon.py::sink_flowing()`: write a unique marker to the
container's own `/dev/log` with `logger` and look for it in `docker logs`. Absence of log
output proves nothing (§4.1); a marker that comes out the far end proves the whole path.

### 3.4 Reading a console command's output at all

`consoleCommand` answers only `Command '<x>' executed.`; the command's real output goes to
the server console and surfaces in the container log as `Console: <text>`. So the only
headless way to read it is to mark the log, run the command, and read the tail back —
**sliced on ValheimRcon's own echo, never on a clock.** Two measurements make the clock
unusable:

- supervisord stamps the container log at **one-second** resolution, so a nanosecond
  `--since` cursor can be *later* than the stamp on output produced after it, and
  `docker logs` drops that output. MEASURED: an `objects_count` printed its whole prefab
  table and a `--since 17:57:35.087Z` read returned nothing.
- Truncating the cursor to the second is not enough either. MEASURED at 22:59:20Z:
  `docker logs --since 2026-09-15T22:50:00Z` returned **8,061 lines** and
  `--since 2026-09-15T22:59:20Z` returned **0**, for a command whose output was in those
  8,061. Docker's own stamps lag the write by seconds, so **no absolute cursor is safe.**

The echo has neither problem: the server logs `Command '<cmd>' executed.` **after** the
command's console output, MEASURED and visible in that order, so the output is exactly the
`Console:` lines between the previous echo and this command's echo.

A read that cannot find its own echo **raises** (`console.py::OutputNotFound`). The caller
must be able to tell "the command printed nothing" from "I could not find the output",
because the second read as the first is how a healthy world got restarted.

### 3.5 A transport receipt is not an execution result

`InvokeConsoleCommand.OnHandle` returns `Command '<x>' executed.`
**UNCONDITIONALLY.** It reports that the bridge accepted a string and handed it
to the main thread. It never carries the console command's output, and it cannot
tell a verb that worked from a verb that is not installed. MEASURED 2026-09-19 —
three probes, three different truths, three identical receipts:

```
objects_count id=Beech1 center=..   Error: Entity id center=520 not recognized
                                      -> the VERB RAN, the ARGUMENT was wrong
objects_remove id=none center=0,0   Error: Entity id none.. not recognized
                                      -> the VERB RAN
spawn_object Beech1 pos=0,0,0       Unknown command 'spawn_object'
                                      -> the VERB IS ABSENT
```

All three RCON replies said `executed.`

**Rule: a `consoleCommand`'s result is read from the container's own stdout log —
`/var/log/supervisor/valheim-server-stdout---supervisor-*.log`, INSIDE the
container — or from a census. Never from the RCON reply.** The two signatures
are the whole diagnosis, and there is no third:

```
Error: ... not recognized     the verb EXISTS and rejected your argument
Unknown command '<verb>'      the verb IS NOT INSTALLED
```

The defect it prevents, priced: the only constructive verb this harness has was
absent from the server for a whole write window (§16.2) while every call kept
answering `executed.`, so a `terrain_write` deleted a `_TerrainCompiler` and put
nothing back. **Nine hours passed before anyone read a log line instead of a
reply** (§16.3).

**THE LOG IS NOT AN ARCHIVE, AND YOUR OWN PROBING IS WHAT DESTROYS IT.** MEASURED
2026-09-19: `/var/log/supervisor/valheim-server-stdout---supervisor-lu0ticfd.log`
**rotates at 1 MB**, and it rotated **six times in about 26 minutes** — the oldest
surviving file (`.6`) began at **16:09** while the incident being investigated was
at **15:58:47**. **Effective retention that night: about 25 minutes**, and the
traffic burning it was the agents' own `objects_count` probes, two log lines per
call. One audit alone sent 435 of them.

So: **if a log line is your evidence, copy it out of the container the moment you
read it** — into the ledger as an `observe`, or into your report verbatim. And
prefer evidence that does not expire: a live census answers the same question
tomorrow. The compiler audit that settled the terrain damage was grounded on a
bounded native `findObjects` census for exactly this reason
(`terrain-and-roads.md` §1.5), after the log lines it wanted had already rotated
away.

---

## 4. Readiness: the RCON port is not the world

### 4.1 `Start listening rcon` is not readiness

MEASURED on this host: the RCON port binds anywhere from **20 s to 200 s** after container
start — **200 s on a cold start with 117 plugins** — because the port is only opened once a
Harmony finalizer on world load runs. A live start on 2026-09-17T13:34 logged
`rcon listening at t+230s` (wall time 222.73 s).

Real readiness is **`Game server connected`** in the container log. Wait for that, not for
the port. INFERENCE from the boot ordering: a client that connects between port-bind and
world-up sees a server that cannot serve.

**Absence of log output is not evidence of anything.** MEASURED on Ulfsland: six
consecutive `Connections N ZDOS:` heartbeat lines exactly **600 s** apart (16:24:24,
16:34:24, 16:44:24, 16:54:24, 17:04:24, 17:14:24). The heartbeat is a **ten-minute**
signal, not the ~30 s it was assumed to be, so a sound "did a new line appear" test needs a
window of `LOG_LIVENESS_WINDOW_S` = 2 × 600 + 60 = **1,260 s (21 minutes)**. That is
unusable inside a build loop, and every shorter window is a coin flip: **a 6 s sample called
a healthy server wedged, and a 40 s sample called a wedged server healthy.** So the log is
read for **diagnosis only**, after a stall has already been detected by the probe.

### 4.2 Ask the game directly

`rcon.py::Rcon.probe()` sends `players`. Because `RunInMainThread` is unconditional and
awaited, a reply **proves the Unity main thread ran at least one frame** since the command
was queued. Reply is `Online N` — MEASURED **8 bytes**, RTT **32–35 ms** over n=12 on an
idle Ulfsland. `MainThreadStalled` is raised if it does not come back inside the deadline.

Raising the deadline costs detection latency and never correctness; lowering it below a
real GC or save pause is the only way to be wrong.

### 4.3 REFUSAL: the watchdog that killed a terrain write

MEASURED 2026-09-16, and it cost work:

```
valheim-watchdog.timer, every 5 minutes:
  if save_age > 1500s AND log_age > 420s  ->  docker restart

both inputs are effectively LOG-DERIVED
a census burst kills the container's log sink while the game stays healthy
=> log freezes -> watchdog concludes "wedged" -> SIGTERM mid-terrain-write
the server answered `players` in 0.03 s the entire time
```

It SIGTERMed the container at **09:27:59**, mid-emit, and cost **two ledger records whose
commands had already been sent**. The watchdog was measuring the log sink and reporting on
the game. It fires precisely when a build is working hardest.

The fix, both halves, now in `hostops/lib/common.sh`:

1. `RCON_BIND_GRACE_S=${VALHEIM_RCON_BIND_GRACE_S:-300}` — a boot window, because **a
   BOOTING server is indistinguishable from a WEDGED one to a socket probe.** Proven live
   2026-09-16: the probe answered "no answer" in **59 ms** for a perfectly healthy Ulfsland
   because the container was 60 s old and 2458 was still refusing — the correct reading of
   the socket and the wrong verdict about the world. Without the grace, the wedge test could
   restart a world that was already restarting: a loop that never converges and **writes
   over a save every time round**.
2. `rcon_answers <world>` — checks the boot window **first**, then does an 8-byte
   `players` round trip against the container IP on 2458 before any restart decision.
   Verified in both directions: **95 ms** for the live world, correct refusal for a stopped
   one, shellcheck clean. A frozen log with a responsive server now logs "sink fault, NOT a
   wedge; left running".

Before a long write, check whether the deployed watchdog is the fixed one: `/srv/valheim-portal`
is a **deployment pull** and the timer runs from there, so a repo fix is not a live fix.
Assume the old behaviour until proven otherwise, keep the save fresh, and mask the timer for
the duration of a long write if there is any doubt.

---

## 5. Saves and backups: verify the save LANDED

**A `save` command returning is not a save.** Verify the postcondition:

1. `World save (5/5) done` in the container log. Full sequence, MEASURED 2026-09-17T13:48:
   ```
   save: World save started
   World save (3/5) DB2 writing done [50ms]
   World save (4/5) FWL writing done [25ms]
   World save (5/5) done. Total time [168ms]
   ```
2. **World-file mtimes moving** on disk.

Save at **every stage boundary** with a verified postcondition — wall, boathouses, town
quarters, lighthouse, then each road leg — and record the `save` as a ledger op (there are
**123** `save` records in the Ulfsland ledger; two consecutive landed saves were confirmed
at 22:50:34 and 22:52:21 on 2026-09-17).

**Back up before your first write:** `hostops/backup_valheim_world.sh <World> <label>`.
This requires `VALHEIM_ROOT` — MEASURED failure 2026-09-18: an agent's pre-write backup
"succeeded" as a no-op because the script refused for want of `VALHEIM_ROOT` and exited
without touching anything. `export VALHEIM_ROOT=/media/big4/projects/game/valheim` first,
and check that a backup file actually appeared.

Note for anyone reading boot markers: `Load world` and `DungeonDB Start` are Info-level and
were trimmed out of the log on 2026-08-15 (MEASURED: 1,464 of the last 2,000 lines were
Info before, 0 of 304 after). `Start listening rcon` and `Game server connected` still
appear.

**A container stop is not neutral to world writers.** An agent mid-batch has spawns and
terrain writes that exist only in the running process until a save lands. Before any stop:
every writer finishes or suspends at a **batch boundary**, saves, verifies the save on
disk, and announces it.

---

## 6. The ledger: the artefact that makes a build replayable

`tools/jumpstart/ledger/` — `schema.py` (1,057 lines), `writer.py` (1,623),
`live.py` (220), `replay.py` (1,280), `project.py` (903), and
`runs/Ulfsland/ledger.jsonl`.

State of the Ulfsland chain at 2026-09-18T03:52:20Z, seq 2478, 2,483 lines:

```
world_manifest  1      observe        488     note             97
zones_generate  242    objects_clear  854     spawn_plan      158
spawn           185    portal          59     terrain_write   100
retire          173    save           123     zones_restore     2
chain_reset       1
```

### 6.1 What it is

Not documentation. A **replay artefact**: an append-only, hash-chained record of every
world-mutating operation, in order, carrying enough to reproduce the operation
byte-for-byte **and** enough to detect that the world it is being replayed into is no
longer the world it was recorded from. The second half is the hard half.

Envelope: one JSON object per line, UTF-8, no embedded newlines. Record keys as they exist
on disk: `seq`, `ts`, `actor`, `op`, `params`, `wire`, `requires`, `expect`, `meta`,
`prev`. `prev` is the digest of the preceding record — that is the chain. Beside the
chain: `blobs/<sha256>` (content-addressed plans, TCData, lattices) and
`blobs/index.json`.

`seq` 0 is the `world_manifest`: `fwl_sha256`, `game_build_anchor`, `db_bytes`, host, the
world modifiers read from `globalKeys`, and the **mod set with a digest** (120 mods on the
Ulfsland manifest). Replay refuses on mismatch rather than substituting.

### 6.2 Append-only, and never edit a record

Corrections are **later appends**. Two live examples:

- Records before **seq 1697** claim reversibility by ZDO id. They are **annotated by a
  later append**, not edited.
- A stray `observe` at **seq 933** was written by a dry run that should not have appended.
  The record stands; the *cause* was fixed (see §6.5).

Durability and concurrency, both MEASURED and both load-bearing:

- Append is **atomic and fsync'd per record** before `append()` returns. A replay driver
  that crashes mid-build must be able to trust that the last readable line really happened;
  a buffered writer that loses the tail turns "resume from seq N" into "build seq N twice".
- `_append_raw` takes an **exclusive `flock`** on `ledger.lock` and **re-reads the chain
  head from the file inside the critical section**. Both halves are needed: MEASURED, the
  old code read the head, computed `prev` and wrote with no lock and with the head **cached
  at open**, so two concurrent appenders **forked the chain at file line 47** and every
  subsequent append refused for every agent. The lock alone would not have helped — the
  forking builder had been open four minutes. The next `seq` comes from the **last line**,
  never from a count, because after a fork the numbers are duplicated and a count hands out
  a seq that already exists twice.
- The lock **covers** the cross-record checks (portal two-ends, one-compiler-per-zone),
  because those decide by reading prior records. It **does not** cover the RCON round trip —
  one agent's latency must not serialise everybody's logging.
- The console token cannot substitute for the file lock: `note` and `observe` send nothing
  and take no token.

### 6.3 The durable key is prefab + world position, NOT ZDO id

**ZDO ids are reassigned on world load.** MEASURED 2026-09-16 by censusing the same
segment across a restart:

```
414 objects, byte-identical x/y/z, and EVERY id different:
  23163:1 -> 22958:1
  76580:1 -> 76288:1
and the offset is not even constant.
```

So: key every record — above all every **removal** — on `prefab + world position`. Record
every removal **with its reason and its position**, so it is reversible *by position*. A
ZDO id is usable only *within* one census, to distinguish "a piece stands here" from "two
pieces stand here".

SEED-DERIVED vs SAVE-DERIVED, applied: positions and prefab names are durable across a
regeneration of the same seed (the plan that produced them is SEED-DERIVED); ZDO ids are
SAVE-DERIVED and do not even survive a restart.

**"World position" MUST include Y, and one module in this repo says it does while
doing otherwise.** MEASURED 2026-09-19: `ledger/project.py:97` folds on
`qkey(prefab, u, v)` — prefab plus the two HORIZONTAL axes — under a docstring
that promises prefab + world position. Instrumenting the fold: **32,946
placements applied, 12,550 collapses, of which only 689 joined pieces at the
SAME y** and **11,861 joined DISTINCT stacked pieces, worst separation 38.00 m**,
leaving **19,777 survivors**. At Ulfsgard `fb_fortress_portcullis` reads
**6 -> 3** (lower row y 23.774, upper y 29.481, same three xz) — the sea gate
rebuilds halved. `world-generation-and-replay.md` §5.5.4 owns the consequence;
the repaired counts are `[PENDING FoldKeyFix]`.

**And the shape underneath it is the one to recognise: ONE module carried TWO
definitions of "the same object".** A retire matches with
`RETIRE_MEASUREMENT_SLACK_M`, a **3D sphere that compares y**; the placement key
does not compare y at all. So the same file can call two pieces one object when
folding and two objects when retiring. **One question, one authority** — when a
module answers "are these the same object?" in two places, the two answers will
diverge and only one of them will be tested.

### 6.4 Four invariants enforced at write time

So that a class of defect becomes unreproducible by construction rather than by memory:

1. **`expect` is mandatory on every mutating op.** An operation that cannot state a
   postcondition is a fact worth failing on, not a field worth skipping. The night's
   pattern was operations that *appeared* to succeed.
2. **Flatten is FORBIDDEN under an over-water structure.** A structure op may declare
   `params.flatten = "FORBIDDEN"`; a `terrain_write` whose footprint overlaps one is
   refused. This is the `early-dock` defect: the pad levelled the rectangle, the water
   under the pier went away, and an over-water body stood on dry ground reading as the
   floating defect.
3. **One zone holds one compiler.** `Heightmap::GetAndCreateTerrainCompiler` returns the
   **first** compiler in a zone, and the write path issues
   `deleteObjects -zone ZX ZZ -prefab _TerrainCompiler -force` before spawning. So a second
   `terrain_write` to a zone **destroys the first one's terrain**. A 20 km ribbon crosses
   ~300 zones and will meet pad zones, so this is the normal case. A `terrain_write` to an
   already-written zone is refused unless it names the earlier seq in
   `entries[].merged_from` and sets `merge_policy`.
4. **A portal tag has exactly two ends.** `Game::FindRandomUnconnectedPortal` pairs on
   exact string equality and then draws `Random.Range(0, count)`. One end pairs with a
   random mod shrine — the operator's one-way trip. Three ends make the pair
   nondeterministic on every load. Uniqueness is on (tag → exactly 2 portal ZDOs), checked
   at `close()`.

**There is no repair mode and no fixup mode.** On drift the replay stops and reports. Every
silent substitution this project has paid for — a stale cache entry, a name collision
resolving to the smaller of two castles, a zero-byte body winning resolution — would have
been cheaper as a refusal.

### 6.5 Emit the record from the library that does the write

`ledger/live.py::LiveBuilder` is **the only sanctioned way to write to the live world**,
and `emit()` does three things in this order:

1. **Append first.** The record is validated (missing `expect`, clobbered zone, duplicate
   portal tag, unmodelled param all refused here) and fsync'd **before anything is sent**.
   A record of something that may not have happened is recoverable — the replay's guard
   measures before it acts. An **unlogged mutation is not reproducible at all**.
2. **Send through `replay.send_wire`** — the *same* function the replay driver uses, so the
   transport that built the world is by construction the transport that rebuilds it. Three
   transports exist and picking the wrong one fails silently.
3. **Verify the postcondition** with the same `replay.verify_expect`, which **waits** for a
   staged operation to settle rather than measuring once. If it does not hold, `emit`
   **raises** `PostconditionFailed` and repairs nothing: the record stays in the ledger and
   the world stays in whatever state it reached. MEASURED live twice: measuring a staged
   Upgrade World op immediately after `start` reported work the world actually did as
   failed, and it aborted a road segment after three good batches. Build and replay must
   *measure* a staged op the same way for the same reason they must *send* it the same way.

**THE HOLE THIS RULE CLOSES, and it was the most damaging one found in the whole build.**
MEASURED and ruled on 2026-09-16:

> `flatten.py apply` sends `deleteObjects` and `spawn_object _TerrainCompiler` **straight
> to RCON, outside the ledger.**

So the single most destructive operation in the pipeline — writing a terrain compiler into a
zone — was the one operation the clobber guard and the `flatten: FORBIDDEN` guard **could
not see**. An ad-hoc `spawn_object` or a hand-run `objects_remove` is invisible to them, so
a bridge placed outside the ledger can be flattened by a road segment inside it while the
guard that exists to prevent exactly that sits unfired.

**The rule:** drive `flatten.py` as a **library** — `zones_for`, `run_patchscan`, `build`,
`write_entries`, plus its `ungenerated_zones` and `pad_location_check` gates, and
`blob_sha256` per zone — and **emit the `terrain_write` yourself**. Never `flatten.py apply`.
And because you are bypassing `apply`, **you** are responsible for sending the compiler spawn
through the ledger: verify after the first unit that the `_TerrainCompiler` actually exists
at the zone centre and that the pad height reads as intended in the world. **The library's
return value is a plan, not a confirmation**, and the difference between those two cost most
of one night.

Two more rules from this file that are cheap to obey and expensive to relearn:

- **The actor is the caller's, keyword-only.** A module-level `ACTOR` constant is a default
  in disguise: `clear.py`'s said `RoadClear` while `RoadEmit` was running it, and the ledger
  recorded work under the name of an agent that had yielded an hour earlier. **Provenance
  that is wrong is worse than provenance that is missing, because it reads as evidence.**
- **A dry run does not append.** MEASURED: `clear.py emit --dry`, run only to find out
  whether the POI gate would refuse, left `observe` seq 933 in the chain under the wrong
  actor's name. `led.build()` now produces the record and `schema.validate()` still runs —
  that is the useful half of a dry run — but nothing is written, sent or verified.
- **A dry run that reaches the ENCODER writes to the watched directory, and that is
  a second, separate half of the same rule.** MEASURED 2026-09-19:
  `terraform/flatten.py::write_entries` lands the data-entry YAML as a **side effect
  of COMPUTING the pad blob**, before anything is emitted, so `harbour_b.py build
  --dry` left `terraform_sudrvik_vestrbakki_1.yaml` in the WATCHED directory having
  opened no socket and written no ledger record. The consequence is the dangerous
  part: **a file in the watched directory is not evidence that anything was built**,
  and an agent's dry run reads from the filesystem exactly like a live write. It
  nearly was read as one. A dry run may compute; it may not leave an artefact
  anywhere a watcher, a later agent or an operator will read as state.
- `observe(what, method, tool, value)` **enforces** that `method` begins `MEASURED` or
  `INFERRED` and that `tool` names the script, because a number whose origin is a transcript
  cannot be told apart from one computed off another agent's clobbered global (§9.3).

### 6.6 Replay is a PROJECTION, not a re-run

The ledger is a **history containing mistakes followed by repairs**: a lighthouse built to
272 pieces then retired; 18 portal arches placed then relocated into a hall; 764 objects
cleared then re-cleared; roads graded four times; 64 duplicates de-duplicated; a Longship
berthed then retired as aground. **Replaying it literally repeats twenty hours of defects.**

So fold the records to **final state per `(prefab, world position)`**, honouring every
`retire` and re-place, and emit *that* — **with the key the phrase actually names. The
shipped fold does not: `project.py:97` keys on `(prefab, u, v)` and drops y, which
collapses 11,861 distinct stacked pieces into their ground course (§6.3). A projection
is only a repair where its key is exact.** What survives the fold, by record type:

| Record | Carries | Reconstructible |
|---|---|---|
| `spawn`, `spawn_plan` | prefab, exact world position, rotation; plan blob with a `plan_sha256` that recomputes (verified e.g. `237cc9b635bb…`) | yes, SEED-DERIVED |
| `terrain_write` | its blob digest **and** the `generated_heights_sha256` it was computed against | yes, and refused per zone if the generated field moved |
| `objects_clear`, `retire` | per-object prefab + position + the reason | yes, reversible by position |
| `portal` | tag, both ends | yes, and the two-ends invariant is checked |
| `observe`, `note` | measurement + provenance | evidence only, not replayed |

Drift detectors the manifest and `requires` give you, each with the defect it prevents:

- `header.mods` / `requires.mods` — the mod set decides what `ZoneSystem::PlaceLocations`
  puts in a zone. More_World_Locations contributes **190 POIs**. A replay on a different mod
  set can flatten a pad on top of a location that did not exist when the pad was solved,
  which is exactly how the old world lost POI content.
- `terrain_write.entries[].generated_heights_sha256` — a TCData sample is a **delta** applied
  to the generated height (`TerrainComp::ApplyToHeightmap`). If the generated height changes,
  the same delta puts the ground somewhere else **silently**, and the first symptom is a
  building in a hole.
- `spawn_plan.params.body.sha256` — a blueprint body is a file on a host outside this repo. A
  body that resolves to a different file is a different building.
- `requires.prefabs` — **`spawn_object` on an unknown prefab is a no-op with a cheerful
  echo.** Probe every prefab before the first command. A replay must not assume a prefab in
  an old record still registers: MEASURED 2026-09-17, `RavenwoodPiecesPlugin::AddRWPieces()`
  has a body of literally `ret`, 1 byte, so the 113 `mm_*` prefabs are **never registered**
  while `fb_*` are.
- `read_fwl()` reads seed, version and worldGenVersion out of the `.fwl2` header
  (4-byte length, then a ZPackage of int32 version, string name, string seedName, int32
  seed, int64 uid, int32 worldGenVersion) — the cheapest possible "am I pointed at the
  right world" check, so a replay pointed at the wrong world stops at line one rather than
  at zone four hundred.

---

## 7. IDEMPOTENCY: the property that makes a build replayable

### 7.1 The defect, priced

A builder with **no read-before-write census gate places everything again on a re-run.**

MEASURED, 2026-09-17: the north lighthouse `lh-north` was left at **272 of 1,255 pieces**
when a `spawn_plan` (**ledger seq 2164**, 28 batches) timed out part-way through because the
game's stdout pipe backed up. Its `expect` is a **full-body `prefab_count` at tolerance 0**,
so **re-emitting it would have doubled all 272 standing pieces**. It had to be **retired
whole and rebuilt**, recorded at ledger seq 2165–2166, on the precedent of **ledger seq 73**.
That cost an agent's entire budget.

The same shape produced **64 duplicate pieces** from a y-blind census (§8.3).

> A read-before-write census gate is the difference between a build that can be replayed and
> one that can only be run once on virgin ground.

### 7.2 The pattern, specified

Implemented in `settlements/waterfront.py` (`lh_census`, `retire_geometry`) and
`settlements/retire_partial.py`. Implement it this way:

**Step 1 — census the site, live, UNSCOPED, over a contiguous y stack.**

```
audit(srv, x0, x1, z0, z1, y_centre, y_levels)
  disjoint CENSUS_CELL_M = 16.0 m cells on a GLOBAL lattice (so two runs agree)
  per cell: ONE unscoped `findObjects -near cx (y_centre+dy) cz 8` per y level
  y levels: contiguous for the body's real height
            CENSUS_Y_LEVELS  = (0, -16, +16, -32, +32)   # default, +/-32 m
            TOWER_Y_LEVELS   = (-8, +8, +24, +40, +56)   # a 48 m tower
            MEASURED: at half-extent 8 each box spans y +/- 8 about its own
            centre, so offsets 16 m apart TILE EXACTLY
  >18 rows  -> re-ask as eight sub-boxes that TILE the parent
  dedup by (prefab, round(x,2), round(y,2), round(z,2)); keep multiplicity
  distances computed LOCALLY, never asked for
```

Proven on one corridor: identical ZDO id sets versus the old per-station survey, 0 either
way, 183 objects, at **107 socket calls / 645 echoed lines** per-cell versus **588 / 988**
per-station.

**Step 2 — split the census into MINE and FOREIGN.** A body piece is one of the plan's
prefabs standing **inside the body's own measured reach**. The prefab name alone cannot
decide it: MEASURED at `lh-north`, the pier 19 m away uses three of the same prefabs.

**Step 3 — diff against the plan and place only what is ABSENT**, reporting
**placed/skipped per prefab**. Batch the absent rows at `BATCH_PIECES = 48` /
`BATCH_BYTES = 1800`, with a `sink_gate()` between records.

**Step 4 — per-record postcondition that cannot pass on a neighbour's work.** Because
`findObjects -near` is a cube, a record's sentinel count must be computed against every
piece of that prefab **in the whole plan**, not just the ones in this batch
(`harbour_city.py::sentinel`).

**Step 5 — if the site must be emptied instead, retire by geometry, and refuse rather than
guess.** `retire_geometry()` is the reference:

- One shared cube for the whole retire, sized from the pieces that **are** standing:
  `need = max(cube_offset(piece, cx, yc, cz))`, `r_cube = ceil((need + 0.5) * 10) / 10`.
  `cube_offset` is the **max of the three axis offsets**, not a distance, because
  `deleteObjects -near` is a **cube in all three axes**.
- **Cap 20 m**, and raise if the body needs more: `deleteObjects` echoes ~155 B per deleted
  object.
- **Refuse if any foreign object sharing a prefab with the body stands inside the cube plus
  `RETIRE_CLEAR_M = 1.0` m.** "A demolition that takes a neighbour's piece is not a repair."
- **The emptiness cylinder is not the cube.** `objects_count` measures an **XZ cylinder**,
  so the proof-of-empty radius must cover the pieces' **radial** spread, not their axis
  offsets. Caught on a synthetic census: a piece at dx=3, dz=3 needs a cube half-extent of
  3.0 but sits **4.243 m** away radially, so a 3.5 m cylinder would have verified "gone" over
  a disc that never contained it.
- **An empty census answer is not a zero.** `retire_geometry` raises on it: it is either an
  already-clean site or a blind census, and the difference matters.

**Step 6 — record the retirement**, with per-prefab counts before, the reason, and the seqs
it retires. `retire_partial.py` writes exactly that, including
`expect={"absent": [{prefab, pos, max}]}` so the demolition has a postcondition too.

### 7.3 Batch to the cheapest interrupt point

**Measure what an interrupt costs BEFORE choosing an order.** MEASURED 2026-09-18: an agent
reported "**0 of 58 pieces placed**", which made its interrupt cost **zero**, and the order
of a whole night's work was changed on that one number — the stop was moved to *now* rather
than *after*, because the cost would never be zero again. Conversely, the 1,255-piece record
had no gate anywhere inside it, so its interrupt cost was a retired-and-rebuilt structure.

When you report state, report it as **placed of total**, so somebody else can price the
interrupt.

---

## 8. Census instruments and their boundaries

### 8.1 Scoped versus unscoped counts disagree

Recorded in the 2026-09-18 build brief: **a prefab-scoped count under-reported 272 where an
unscoped census found 352.** A scoped count can only see prefabs you wrote down, so it is
structurally blind to whatever else is standing on the site — which at `lh-north` included a
pier that shares three prefabs with the body. Use an **unscoped** census to decide "what is
here", and scoped counts only to verify a body you already enumerated.

### 8.2 `objects_count` is an XZ cylinder with a boundary comparison

MEASURED in `settlements/build.py::check_radius`:

```
stenvik-hut-1's 28 pieces sit on exact rings from its anchor:
  1.000 m (x4)   1.414 (x8)   2.003 (x1)   2.236 (x15)
At a radius of EXACTLY 1.000 the check answered `want 4 got 2`
  -- four pieces on the boundary, two of them counted.
The same body re-counted at 3 m answers 28 for 28 with the plan's exact histogram.
```

That is a coin flip, not a postcondition. **Snap the radius to the MIDPOINT of a gap
between rings**, which leaves every piece unambiguously inside or outside. And a refusal is
a real answer: if no gap-midpoint that contains at least one piece fits under the bound
(body reach, cleared radius, distance to the nearest sibling), the caller is told rather
than handed a number.

Two more `objects_count` traps, both MEASURED, both in `replay.py::Server.count`:

- **An empty `ignore=` is not "no filter"** — it is an empty id. The console answers
  `Error: Entity id  not recognized.` and prints no `Total`, so the count refuses and the op
  aborts on a malformed command rather than on a wrong world. The token must be **absent**,
  not empty.
- **The filter must never mask the thing being counted.** MEASURED on 16 built zones:
  `objects_count _TerrainCompiler ... ignore=_*` answers **0** at every zone centre while the
  same query with no `ignore` answers **1** — because `_TerrainCompiler` matches the default
  `_*` glob. A `prefab_count` check on any underscore prefab therefore reported terrain that
  is demonstrably on disk as **absent**, and every future `_ZoneCtrl` check had the same hole
  waiting. `count()` now drops the `ignore` automatically for a concrete underscore id, and
  keeps it for the deliberate `id=*` query.
- `id=*` is refused above a **40 m** radius: a freshly generated zone answers `Total: 6149`
  followed by one line per prefab, straight to the console sink — the query that wedged it.
- **No `Total:` line is a stop, not a 0.** "An empty answer is not a zero — that is how a
  removal gets aimed at nothing."

### 8.3 `-near` is a CUBE in all three axes — for both verbs

MEASURED 2026-09-16: `findObjects -near` is a cube, not a sphere and not a disc. The old
per-station survey truncated in Y: `wood_beam_1` answered **2 rows for a 7.42 m box and 8
for a 16 m one**, because six pieces stood at **y 84–85**, 8–9 m up. **The survey that
decides "is there a building here?" was blind to buildings above it** — exactly the
structures a removal cylinder is supposed to skip.

`deleteObjects -near` is a cube too, and it **grazes**: MEASURED, an object at **20.0 m
survived a 20.0 m delete**. Centre the retire on the thing rather than reaching for it.
Separately MEASURED in `roads/clear.py:243`: a 20 m box centred on (90.8, -80.8) answered
with a trunk **21.9 m away in XZ** whose every axis offset was under 20 — the cube metric,
not the distance.

**The 64-duplicate defect, in full, because it is the canonical idempotency failure:**

```
settlements/waterfront.py: a census was ASKED for a contiguous 0..+64 m y stack
and silently measured the module default -32..+32, because the loop kept
iterating CENSUS_Y_LEVELS after y_levels became a parameter.
MEASURED consequence at lh-north: it saw NOTHING above y 72.98 --
  0 of 48  wood_roof
  0 of 16  wood_wall_log_4x0.5
  0 of  2  bonfire
The per-piece diff therefore read 83 standing pieces as MISSING.
64 of them were re-sent before the cumulative count caught it at 1319 for 1255.
Repair: remove BOTH copies at each duplicated position and re-send the plan's own
line, so the position ends with exactly one piece carrying the plan's rotation.
```

**A shrink is silent.** That is why the y window is now reported inside the record, and why
`unlisted_boxes` and `socket_calls` are returned alongside every census.

The old subdivision had the same class of defect in the other direction: children expanded
by the **half-diagonal** covered **2.83×** the parent volume and reached into the
neighbouring cell. Children now **tile** with a 1.02 float margin.

### 8.4 Cheap first, then tighten

`replay.py` counts with the cheap `objects_count` **first** and quarters the disc only when
the count says it must. A 20 km corridor censused prefab-by-prefab costs 139 names per
region through the console sink; the cell census costs 107 socket calls per cell and
answers the question nobody wrote down.

**And the cost asymmetry is not a preference, it is what stops the server.**
`objects_count`'s reply is a short table; `findObjects`' reply is **every row**, and
`RconProxy.HandleCommandAsync` logs the FULL reply inline on the Unity main thread
(§3.1). MEASURED 2026-09-19: a `findObjects` cell census at a **0.06 s** throttle,
**~1,900 calls in 21 minutes**, wedged the server — the same pressure that rotated a
1 MB log in 25 minutes and destroyed the incident evidence (§3.5). **The instrument,
the thing that destroyed the evidence and the thing that stopped the server were one
activity.** Screen with `objects_count`, and spend `findObjects` only on the cells
that came back non-zero.

The gate that census now runs under, and it is the shape to copy:

```
THROTTLE           0.15 s, stated as a MAIN-THREAD BUDGET with the wedge as its reason
LIVENESS           every 20 cells: `players`, 15 s deadline
ON NO ANSWER       sleep 30 s and retry, max 10 -- BACK OFF, never pile on.
                   Retrying in a tight loop FEEDS the wedge.
ON FINAL FAILURE   ABORT and write NO partial census file
```

**That last line is the load-bearing one: a short census must never read as a clear
road.** A partial file with fewer obstacles in it is the truncated-reply defect
(§8.5) wearing a third hat, and nothing downstream can tell it from a clean corridor.

### 8.5 An instrument that abbreviates for human comfort LIES to a parser

MEASURED 2026-09-19, and it is the third instance of one class in a single night.
`rcon.py`'s CLI carried `--max-print`, defaulting to **600 characters** — a
courtesy for a human reading a terminal, applied to output an agent parses:

```
findObjects -near 520 50 824 8
  header says     "Found 41 objects"
  rows recovered   6
```

It was caught only because the same census was run through two transports and
they disagreed: **1,102 objects via the CLI against 1,408 via the socket**, with
**identical subdivision counts** — which proves the server returned identical
headers both times and the loss was entirely on the reading side.

FIXED: `--max-print` is **unlimited when stdout is not a TTY**, 600 on a TTY, and
`0` means unlimited. Verified live on the same query: **header 41, parsed 41.**

**Rule: on every call, compare the reply's DECLARED count against the number of
rows you actually parsed, fail loudly when they differ, and SUBDIVIDE the query
rather than accepting the page.** A count that disagrees with its own list is a
failed read, not a short answer — and nothing downstream can tell the
difference, because a truncated page is shaped exactly like a whole one.

Its two siblings, so the family is recognisable on sight:

- **The delivery receipt** (§3.5): `Command '<x>' executed.` is shaped like a
  result and is only a receipt.
- **The 4050-byte packet cap** (§1.3): `ValidatePayloadLength` truncates an
  outbound reply, so a long listing arrives as a well-formed fragment with no
  marker saying where it stopped. **MEASURED live 2026-09-21: one call declared
  "Found 308 objects" and returned 41 rows — 13% of a Dvergr ruin — and
  `-detailed` truncates at about 29 rows**, because each row is longer. The cap
  is on BYTES, so the row count at which it bites is a function of what you
  asked for and can never be memorised as a constant.

### 8.6 A census that writes into its own input makes every body its own witness

A new shape, MEASURED 2026-09-21, and the cheapest one yet to produce a
confident wrong answer. An agent wrote its own census rows into
`mod_keepout.json` — the file the next run reads to decide which bodies are mod
content — and then re-read them. **Every body became its own witness, and 35
bodies flipped from mod-placed to OURS on the second run**, on no new evidence
at all: the second run was measuring the first run's opinion.

**Rule: an instrument must never read a file it also writes. Where it must, it
rebuilds that input from a snapshot taken BEFORE the census and refuses rows
carrying its own provenance.** Both scripts now carry a self-reference guard and
rebuild from a pre-census snapshot, so **re-running is idempotent** — which is
the §7 property, arriving here as a read-path requirement rather than a
write-path one.

It is the doctrine's "a check whose reference is the thing under test is not a
check" (`structures-and-settlements.md` §3.7) with a file standing in for the
datum, and it is harder to see, because the first run's output looks exactly
like evidence.

---

## 9. Verification

### 9.1 The two-radius check

`waterfront.py::lh_live_verify` is the reference shape:

1. `objects_count id=* ignore=_*,sfx_*,vfx_*` at the **gap-midpoint radius** — the total.
2. **One scoped count per prefab at the same radius** — the per-prefab histogram, compared
   at **tolerance 0**.
3. The **same star count at a wider `stray_radius_m`** to expose anything placed outside the
   body's measured reach, **minus the foreign objects the pre-build census had already
   measured in that annulus**. `strays == 0` is the check.

`sfx_*` / `vfx_*` are excluded because a fuelled piece spawns its own one-shot effects and
an exact count taken immediately after placement would race them.

**The pre-build census is an input to the postcondition.** Without it, "stray" and
"somebody else's pier" are the same number.

### 9.2 Use the per-piece probe as the diff instrument

Recorded 2026-09-18: **a per-piece probe beat a box census** and should be the diff
instrument. The box census answers "how many of this prefab are within r", which cannot
distinguish a missing piece from a displaced one; the per-piece probe answers "is there a
piece at *this* position", which is the question a re-place needs. Note the failure mode it
brought with it (§8.3): a per-piece diff is only as good as the y window of the census
feeding it, so report the window.

### 9.3 A postcondition expected to fail must STILL be run

**A failed postcondition IS the evidence.** Two live consequences:

- MEASURED 2026-09-16: a `save` at **seq 197** failed on the `prefab_count` `ignore` defect
  (§8.2). The segment was verified and the save **re-emitted**, rather than the failure being
  accepted or the check relaxed. **A green check that was wrong is worth less than a red one
  that gets chased.**
- A guard with no data must **refuse, not pass**. Records carrying no `expect.prefab_count`
  left the footprint guard with no extent and it refused the whole zone — the correct
  default. The fix was to **add** each structure's extent by a later append, not to make a
  missing extent mean "unprotected".

### 9.4 A timeout must not be able to produce a measurement

MEASURED: a probe that waited **6000 FRAMES** instead of real time wrote a complete,
well-formed file in which **every prefab read `znet=0`**, because the world had not loaded.
Wait on **real-time deadlines**, and on timeout write **nothing**.

### 9.5 A pattern you have not proved can MATCH returns a confident zero

MEASURED 2026-09-19, and it nearly cost a healthy server. A deploy was checked with
`grep -i worldedit`. **That pattern cannot match `World_Edit_Commands`** — the package
directory is underscore-separated and only the DLL filename `WorldEditCommands.dll` is
camel-case, which needs a recursive search. The pattern was run **four times, returned
zero four times**, and the deploy was declared to have not run; a stop/deploy/start was
launched against a server that had just come up correctly, and was cancelled mid-flight
by the agent that had actually deployed it.

The deploy had in fact succeeded, and the checks that showed it are the ones to copy —
**each answers a question the grep could not:**

```
ls  /opt/valheim/bepinex/BepInEx/plugins/World_Edit_Commands   -> PRESENT
plugin count                                                      98   (was 97)
diff server-plugins.previous vs the new tree  -> ONE line: "> World_Edit_Commands"
three sha256s matching across deploy source, runtime tree, profile cache
```

**Rule: a negative from a pattern you have not proved can match is not evidence of
absence.** Prove the pattern against a known positive in the same run — the doctrine's
"validate the instrument against a known positive before trusting a clean result" — and
prefer an enumeration you can diff over a match you can mistype. This is the same shape
as the `-near` cube that answered 0 because it was probing the wrong y, the
`objects_count` cylinder that could not see a stack, and the filesystem sweep that could
not reach the answer and returned a zero anyway.

**It was not one agent's slip: a second agent used `grep -ci worldedit` for every
"verb still absent" claim it made for an hour, including in a yielded report.** And
that is the worse instance, because **the broken instrument agreed with reality the
whole time** — the mod really was gone, so a pattern that could not match was
confirmed by events every twenty minutes and earned trust it had not got. The
instant the deploy landed it went on answering **0** with the package present.

Two things follow, both cheap:

- **The positive control is ONE command.** Point the pattern at something you know is
  there — `Server_devcommands` and `Upgrade_World` sit in the same directory with the
  same underscore shape — and confirm it answers. Applying rule 3 of the doctrine to
  your geometry and not to the thing you report every twenty minutes is the actual
  failure here.
- **Retract to UNKNOWN, not to the opposite.** Every claim resting on that pattern
  became *"unknown from my side"*, not *"present"*: an installed package is not a
  registered verb, and only the container's stdout probe answers that (§3.5, §16.2).

**The side-by-side, same directory, same second, two patterns, opposite answers** —
run once the package was installed, which is what makes it a control rather than an
anecdote:

```
cd /opt/valheim/bepinex/BepInEx/plugins        98 entries, World_Edit_Commands PRESENT

ls | grep -i -E "worldedit|world_edit|devcommand|rcon"
  -> Server_devcommands · ValheimRcon · World_Edit_Commands        MATCHES
ls | grep -ci worldedit
  -> 0                                                             CANNOT MATCH
```

The agent whose pattern worked recorded it as **luck in the pattern, not virtue** —
it happened to carry the `world_edit` alternative. What was not luck is that it did
not stop at the directory listing: it pulled `ValheimRcon.dll` out of the container
and ran `strings` on it (which is where §16.1's ownership table comes from), then
probed the verbs and read the **container stdout** rather than the reply (§3.5).
**A listing is a screen; the DLL and the probe are the conclusion.**

And the sharpest statement of why this class is dangerous, worth carrying verbatim:
**a false negative that happens to be TRUE is indistinguishable from a measurement
until the world changes under it.**

---

## 10. Sandboxes: how to probe safely, and the trap

### 10.1 The safe construction

When a question needs a running engine, do not ask the live world:

- **A mirror copy of the install, with the live BepInEx excluded, on its own ports, never
  the live world or profile.** Staged in `/tmp` with a throwaway world.
- For IL questions, a **writable copy** of the assembly plus `monodis` /
  `monodis --output=x.il` staged in a scratch directory (`/tmp/bsework` was the established
  path). The live assemblies are at
  `$VALHEIM_ROOT/<World>/data/bepinex/valheim_server_Data/Managed/assembly_valheim.dll` and
  `BepInEx/plugins/…`. **Reading the game's own IL has repeatedly been the thing that
  settled these questions** — including the entire §2 correction.
- Prefab geometry comes from a sandbox dump, not from the live world: **4,644 prefab
  colliders** were dumped off a sandbox server for one measurement, and a `ZNetScene.m_prefabs`
  dump of a sandbox copy with a mod staged returned **4,872 prefabs**.
- For host-script logic, the stubbed-docker pattern in `hostops/tests/` costs nothing and
  touches no container: a `mktemp -d` tree mirroring the real layout, `docker` replaced by a
  stub that records its argv, world root and compose dir supplied through the environment.
  Five tests, about a second, zero host state.

### 10.2 REFUSAL: a subset sandbox measures a different game

This one produced a confident, coherent, **wrong** finding that reached the operator.

```
The claim: 11 custom build-table category NAMES across 6 mods, 439 pieces, all
collapsing onto ONE category id (measured id 10), against Hud.UpdatePieceList's
15 x 6 = 90 icon cells -- so everything past slot 90 is invisible.

The instruments: a census of config keys, and Piece.m_category read from a
ZNetScene dump in a SANDBOX staged with a SUBSET -- Jotunn + JsonDotNET +
OdinShip + RavenwoodVikingHouses + RavenwoodRestorations + ...

Re-measured in a loaded game with ALL 121 PLUGINS and the live config:
  NO merged tab exists. Every mod's category id is DISTINCT.
    RavenwoodRestorations  17 (107 pieces) and 15 (13)
    OdinsUndercroft        19 (34)     OdinShip  20 (17)
    RavenwoodVikingHouses  25 (29)     More_World_Locations  26 (75)
    OdinsKingdom           not on the hammer at all -- its own _GBCastleKit table
  Farmhouse1..20 sit at indices 67..86 -- INSIDE the 90-cell grid, reachable.
```

The id-collapse mechanism is real in the *library* — `GetCategory()` computes a new id as
`Enum.GetValues(Piece.PieceCategory).Length - 1` from a snapshot that never includes its own
earlier registrations — but **the subset was what made every mod collide.** A fix was halted
before it touched anything, and the earlier report was retracted.

**Rule: compare against the full installed set, never a sample.** If you must use a subset,
label every number from it INFERRED and say which plugins were absent.

---

## 11. Agent protocol

### 11.1 One writer at a time, by name

- The ledger/console **token** is held by exactly one agent. Announce **TAKING** and
  **RELEASING** by name, to all peers.
- Take the token only after the previous holder **releases** it *and* any world restart has
  been announced complete (the session used an explicit `WORLD BACK` broadcast).
- **Back up and dry-run your write path before taking the token.** The reason, in the words
  of the agent that did it: "finding a schema refusal while holding a two-phase console is
  exactly how a staged operation gets left half-sent."
- **Anything live you measured before a restart is invalid after it.** The plugin set can
  change under you; re-take live probes after `WORLD BACK` and let them gate the first write.
  Disk- and generator-derived numbers survive (§12).
- Stop **at a batch boundary**, save, verify the save landed on disk, then announce **SAFE
  TO STOP** *and* whether you are releasing the token or holding it across the restart, *and*
  **how many pieces of how many are placed** so the interrupt can be priced.
- The file lock, not the token, is what protects the ledger (§6.2). The token protects the
  *world*.

### 11.2 REFUSAL: the handoff that nobody executed

MEASURED 2026-09-17T01:29, in the orchestrator's own words:

> "That handoff was written *for the next actor*. I was the next actor. I read it,
> summarised it to you, and stopped."

There was no blocker: the token was free, no agent was writing, the world was saved with a
verified postcondition, and the fix that unblocked the remaining segments had just landed and
been proven on twelve segments. The work was **neither finished nor advanced**.

**Hand off explicitly, to a named actor, or execute it yourself.** A handoff addressed to
"the next actor" is a statement, and a statement is not a deliverable. If you are the next
actor, you are the executor.

### 11.3 REFUSAL: the shared Python eval kernel

**The Python eval kernel is SHARED between task subagents, and globals are silently rebound
mid-run.** MEASURED twice in one session:

- 2026-09-16T02:11 — globals `f` and `B` collided between two agents within ten minutes.
- 2026-09-18T02:48 — a surveying agent had `W` and `fl` rebound by another agent's cell: `W`
  became a module object, `fl` became a `Field` instance.

Nothing was corrupted on disk, but **any number computed across two cells could have been
computed against somebody else's variables.** This is also why `LiveBuilder.observe` forces
`method` to name MEASURED/INFERRED and `tool` to name the script: a number whose origin is a
transcript cannot be told apart from one computed off another agent's clobbered global.

**Run anything load-bearing as a standalone script in ONE invocation** — imports, inputs and
computation self-contained — or prefix every global with your agent name. If a published
number came from multi-cell eval state, **re-derive it**.

### 11.4 Deploy-tree discipline

- The dev checkout is `repos/valheim-portal-main`. **`/srv/valheim-portal` is a deployment
  pull — never edit it**, and never let tooling work in the live deploy dir: there is a
  postmortem where a worker's `git clean` wiped untracked files mid-run, in a directory that
  holds production secrets and a running container's bind mounts.
- **A container restart repopulates `<World>/data/bepinex/BepInEx/plugins` from
  `<World>/config_merged/bepinex/plugins`.** MEASURED 2026-09-15: a hand-installed
  `CharacterTemplate.yml` in the `data/` runtime path was silently reverted to an older build
  by the next start. Stage changes in `config_merged`, never in `data`.

---

## 12. SEED-DERIVED versus SAVE-DERIVED: the table to consult before trusting a number

| Fact | Class | Survives a regeneration of the same seed? |
|---|---|---|
| Generated height + biome field (PatchScan/locscan raster of a sandboxed mirror) | SEED-DERIVED | **Yes** — this is why a survey solved against it can be re-run |
| Seed, world name, worldGenVersion (`_main.<n>.fwl2` header) | SEED-DERIVED | Yes |
| Plan rows: prefab, world position, rotation, `plan_sha256` | SEED-DERIVED (they are authored, and blob-digested) | Yes |
| `terrain_write` blob **plus** its `generated_heights_sha256` | SEED-DERIVED, *conditionally* | Yes **only if** the generated field digests the same; refused per zone otherwise |
| Location/POI placement | SEED-DERIVED **and mod-set-dependent** | Yes for a fixed mod set; More_World_Locations contributes 190 POIs, so a different set is a different world |
| Live censuses (`findObjects`, `objects_count`) | SAVE-DERIVED | No |
| Applied terrain deltas as they stand in the world | SAVE-DERIVED | No |
| ZDO poses and **ZDO ids** | SAVE-DERIVED | No — ids do not survive a **restart** (§6.3) |
| Container bridge IP, process pids, fd numbers | RUNTIME | No — re-resolve every time |
| `plan.json` | **NEITHER** | It is the generator's **request** list: 12,301 entries, **every one `placed: false`**. Never resolve against it. Only a live `findObjects -prefab LocationProxy` says what was placed; 60 of 65 live markers near one site were modded POI no seed-derived dump can see. |

---

## 13. PRE-FLIGHT CHECKLIST

Run this, in order, before the first write of a session. Every line has a defect behind it.

```
ENVIRONMENT
[ ]  export VALHEIM_ROOT=/media/big4/projects/game/valheim
[ ]  export VALHEIM_WORLD=Ulfsland          # or the target world; NOT a constant
[ ]  cwd is the dev checkout repos/valheim-portal-main, NOT /srv/valheim-portal

TARGET AND TRANSPORT
[ ]  container is up:   sudo -n docker ps --format '{{.Names}}\t{{.Status}}' | grep -i "$VALHEIM_WORLD"
[ ]  bridge IP resolved FRESH (it changes on every start):
       sudo -n docker inspect valheim-server-$VALHEIM_WORLD \
         --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
     -- if you are testing 127.0.0.1:2458 and it refuses, that is the MAPPING, not the server
[ ]  credentials read from $VALHEIM_ROOT/$VALHEIM_WORLD/config_merged/bepinex/org.tristan.rcon.cfg
[ ]  main thread alive:  python3 tools/jumpstart/terraform/rcon.py --probe
       expect "main thread alive, probe rtt 32-35ms"
[ ]  every game command in your plan is prefixed `consoleCommand ` (or goes through
     Rcon.console / place.py / LiveBuilder). A bare verb answers `Unknown command`.
[ ]  the CONSTRUCTIVE verb is present, proven in THIS window and read out of the
     container's stdout log, never out of the RCON reply (§16.2, §3.5):
       consoleCommand spawn_object <a_registered_prefab> pos=0,0,0
     `Unknown command` => the verb is gone => REFUSE THE WINDOW, and run no
     destructive half of anything until it is back (§16.3)
[ ]  every reply-parsing read you will trust compares the DECLARED count against
     the rows you actually parsed, and `rcon.py --max-print` is unlimited (§8.5)

READINESS (only if you started or restarted the container)
[ ]  waited for `Game server connected` in the container log -- NOT merely
     `Start listening rcon commands`, which binds 20-200 s earlier
[ ]  if within RCON_BIND_GRACE_S (300 s) of start, a refused socket is BOOTING, not wedged

SINKS AND WATCHDOG
[ ]  host-side sink verdict is `healthy`:
       python3 -c "import sys; sys.path.insert(0,'tools/jumpstart/terraform'); \
                   import rcon; print(rcon.host_sink_state())"
[ ]  know the drain command for THIS container before you need it (§3.2)
[ ]  watchdog: is the deployed /srv copy the one with rcon_answers? If unsure, assume the
     old log-derived behaviour, keep the save fresh, and consider masking the timer

SAFETY
[ ]  backup taken AND the file verified to exist:
       hostops/backup_valheim_world.sh $VALHEIM_WORLD <label>
[ ]  world manifest matches: mod set digest + fwl_sha256 as recorded at ledger seq 0
[ ]  every prefab you intend to place is REGISTERED (spawn_object on an unknown prefab is a
     silent no-op with a cheerful echo)

PROTOCOL
[ ]  token: nobody else is writing; you have announced TAKING <token> by name
[ ]  ledger writes go through ledger/live.py::LiveBuilder with actor=<your id>
[ ]  you are NOT calling flatten.py apply (drive it as a library and emit terrain_write
     yourself)
[ ]  dry-run your write path (LiveBuilder(dry=True) validates and does NOT append) BEFORE
     taking the token
[ ]  you know which of your `--dry` paths still WRITE (flatten.py::write_entries
     lands a data-entry YAML in the watched directory before anything is emitted,
     §6.5) -- and nobody reads a file in that directory as evidence of a build
[ ]  all load-bearing computation is in a standalone script run in ONE invocation
```

---

## 14. THE WRITE-CYCLE LOOP

Follow this literally, per stage. It is the loop that survived the night.

```
0.  PRE-FLIGHT (§13). Announce TAKING the token.

1.  CENSUS THE SITE, live, before writing anything.
      unscoped cell census: 16 m global-lattice cells, half-extent 8 m,
      y levels CONTIGUOUS over the body's real height, >18 rows subdivided
      into eight TILING sub-boxes, dedup by (prefab, x, y, z) to 2 dp.
    Report: standing_per_prefab, foreign_per_prefab, nearest_foreign_xz_m,
            y_window ACTUALLY USED, unlisted_boxes, socket_calls.
    An EMPTY answer is not a zero -- stop and find out which it is.

2.  DIFF against the plan. Compute ABSENT = plan - standing (per position).
    If ABSENT is empty: report "already built", skip to 7. This is idempotency.
    If standing > plan at any position: you have duplicates -- retire BOTH copies
    at that position and re-send the plan's own line (§8.3).

3.  DECIDE THE INTERRUPT COST and say it out loud: "N of M placed".
    Order the stages so the cheapest interrupt point comes at each boundary.

4.  EMIT, one ledger record per batch, through LiveBuilder:
      batch = 48 pieces or 1800 bytes, whichever comes first
      record carries: params (prefab set, positions, flatten FORBIDDEN + reason
        where over water), wire (one joined consoleCommand line),
        requires (mods, prefabs, blobs), expect (per-record sentinel
        prefab_count computed against the WHOLE plan), meta (stage, batch i of n,
        pieces, why)
      emit() appends + fsyncs FIRST, then sends, then verifies. It repairs nothing.
    BETWEEN RECORDS:
      sink_gate("<stage> batch i/n")     -> host /proc verdict; drains if drainable
      Rcon.probe() every few batches     -> 8 bytes, ~35 ms, proves a frame ran
    ON A TIMEOUT OR NO REPLY:
      DO NOT re-send. DO NOT SIGKILL. DO NOT restart.
      Run the §3.2 host drain, re-probe, then go back to step 1 -- the census is
      what tells you what actually landed.

5.  VERIFY THE STAGE (§9):
      per-prefab counts at tolerance 0, at a GAP-MIDPOINT radius
      star count at the same radius (ignore=_*,sfx_*,vfx_*)
      star count at the wider stray radius, minus the pre-build census's foreign
        objects in that annulus  ->  strays == 0
    Run a postcondition you expect to FAIL anyway. The failure is the evidence.

6.  SAVE AND PROVE IT LANDED:
      emit the `save` op, then confirm `World save (5/5) done` in the container log
      AND world-file mtimes moving. The command returning is not a save.

7.  RECORD AND HAND OFF:
      append the observations with MEASURED/INFERRED + the tool that produced them
      append any retire with per-object prefab + POSITION + reason
      NEVER edit a record; corrections are later appends
      announce RELEASING the token, by name, to a NAMED next actor -- or keep going
        yourself. A handoff to "the next actor" is not a handoff.
```

---

## 15. Index of refusals — techniques that failed, with their prices

| Technique | Why it failed | Price |
|---|---|---|
| Bare RCON verb without `consoleCommand` | Verb registry closed over its own assembly, 41 verbs; `save`/`players` work and mislead | 1 hour; a healthy world reported broken |
| `findObjects` unscoped / `-detailed` over a body | Listing **is** `CommandResult.Text`, logged in full on the main thread; ~200 KB for 1,907 pieces | 2 container restarts; one poisoned RCON peer (448 pieces at 40 m) |
| Tiled sweep of ~700 small `findObjects` cubes | Same write path, accumulated: 1,082 object lines in ~10 s | 1 wedge |
| `objects_count id=*` over a generated zone | `Total: 6149` + one line per prefab into the console sink | 4 sink failures in one session |
| Looping one named prefab per call instead | Moves the **same** rows plus an extra echo per call | no gain; ask only about prefabs you placed |
| 1,255-piece record with no gate inside it | Backed up the game's stdout pipe mid-placement | `lh-north` retired at 272/1,255 and rebuilt; an agent's whole budget |
| Re-sending a partial plan to "finish" it | `expect` is a full-body `prefab_count` at tolerance 0 | would have doubled 272 standing pieces |
| Census with the default y window on a 48 m tower | `findObjects -near` is a cube; saw nothing above y 72.98 | 64 duplicate pieces; count caught at 1319 for 1255 |
| Radius landing exactly on a ring of pieces | Boundary comparison: `want 4 got 2` at 1.000 m | a coin-flip postcondition |
| `ignore=_*` on an underscore prefab | The filter masked the counted prefab: 0 vs 1 at 16 zone centres | a false-negative save at seq 197 |
| Log freshness as liveness | Heartbeat is 600 s; needs a 1,260 s window | 6 s sample called healthy wedged; 40 s called wedged healthy |
| Watchdog restarting on log age | Measured the sink, reported on the game | SIGTERM mid-terrain-write; 2 ledger records lost |
| `docker restart` to clear a sink stall | Process could not be signalled | timed out at 180 s |
| `dd` one-shot on the pipe | Pipe refills while syslogd catches up | needs a sustained read |
| A verb because the harness's own `STAGED_VERBS` lists it | `terrain_reset` and `objects_reset` are owned by no installed plugin, `UpgradeWorld.dll` 1.82.0 included | latent: the first caller gets `Unknown command` behind an `executed.` receipt (§16.4) |
| A `findObjects` cell census as the FIRST pass | its reply is every row, logged in full inline on the main thread | ~1,900 calls at a 0.06 s throttle in 21 min wedged the server and rotated the incident log away (§8.4) |
| Retrying a silent `players` in a tight loop | the wedge is a full pipe; more traffic fills it faster | feeds the thing you are trying to clear — back off 30 s, diagnose the pipe pair, drain from the host (§3.2) |
| Writing a partial census file when a census aborts | fewer obstacles reads downstream as a clear corridor | the truncated-reply defect wearing a third hat (§8.4) |
| In-container sink probe (`docker exec`) | Probes **through** the blocked thing; reports `fds_lost` spuriously | would have cost a 4–7 min restart for a 2 s repair |
| Empty `readlink /proc/<syslogd>/fd/N` as evidence | Not resolvable from a `docker exec` context even when healthy | nearly a needless restart |
| `--since` clock cursor on `docker logs` | 1 s stamp resolution and stamps lagging the write | 8,061 lines vs 0 for the same command |
| Log-grep echo verification of a batch | Log lines truncate at ~150 chars; batch is ~3,600 | a landed placement read as a failure; a half-built building |
| `flatten.py apply` | Sends `deleteObjects` + `spawn_object _TerrainCompiler` outside the ledger | the most destructive op was invisible to both guards |
| Module-level `ACTOR` constant | Recorded work under an agent that had yielded an hour earlier | wrong provenance reads as evidence |
| `--dry` that appends | Left `observe` seq 933 in the shared chain | a permanent stray record, annotated not edited |
| Unlocked append with a cached head | Two appenders forked the chain at line 47 | every subsequent append refused, for everybody |
| ZDO id as a durable key | Ids reassigned on world load, non-constant offset | reversibility guarantee retracted from seq 1697 back |
| `plan.json` as the world | Generator **request** list; 12,301 rows, all `placed: false` | nearest named location off by 20–74 m |
| Subset sandbox (6 plugins) | Measures a different game | "439 pieces on one category" retracted; a fix halted |
| Frame-counted timeout in a probe | Wrote a well-formed file of `znet=0` | a complete, confident, empty measurement |
| Shared eval kernel for multi-cell state | Globals silently rebound between subagents | two agents; every cross-cell number re-derived |
| Handoff addressed to "the next actor" | The next actor read it and summarised it | work neither finished nor advanced |
| `consoleCommand` RCON reply as a result | `InvokeConsoleCommand.OnHandle` returns `"Command '<x>' executed."` **unconditionally**; identical receipt for a verb that ran, a verb that ran with a bad argument, and a verb that does not exist | 4 agents, ~1 h; one destroyed `_TerrainCompiler`; a lintel design nearly retired on a false negative |
| A manifest entry as proof a mod is deployed | `JereKuusela-World_Edit_Commands` present in all four `ulfsland-*` manifests at scope `client-only`; the server tree never received it | the only constructive verb on the world, missing for 10 h, unnoticed |
| Running the destructive half of a two-part op when the constructive half is unavailable | `terrain_write` = delete compiler -> respawn; clearing = `objects_remove` -> place; retire-replace = `deleteObjects` -> `spawn_object` | `ulfsgard-dredge`'s compiler in zone (16,11) deleted with nothing put back |
| A breadcrumb pid as evidence an agent is alive | `_append_raw` writes it per PROCESS; a finished tool subprocess always leaves a dead pid | a live token-holder declared dead; a second writer on one chain; one failed record |
| `--dry` that reaches the encoder | `flatten.py::write_entries` lands the data-entry YAML in the WATCHED directory while COMPUTING the blob, before anything is emitted | a file in the live data directory from a run that opened no socket |
| Two builders each emitting one shared element | `harbour_city.boathouse()` gives bay N's east apron and bay N+1's west apron the same `deck_strip` | 52 duplicate pieces standing at Ulfsgard: 20 `wood_floor`, 19 `wood_pole_log_4`, 13 `wood_roof` |
| An output cap tuned for a human reader, on a read a parser consumes | `rcon.py --max-print` defaulted to 600 characters | a `findObjects` header declaring **41 objects** yielded **6** parsed rows; two transports censused 1,102 against 1,408 |
| Expecting a container log line to still be there later | the stdout log rotates at 1 MB — six rotations in ~26 min, ~25 min of retention, burned by our own probes | the 15:58:47 incident lines were gone by the time a second agent looked; oldest survivor started 16:09 |
| Auditing zone state through `consoleCommand objects_count` + a log read | two log lines per call through the console sink, plus a settle per zone | 435 zones in **21 minutes** and it destroyed the log it was meant to protect; the native `findObjects -near <centre> 0.00 <..> 2.00 -prefab _TerrainCompiler` did 435 in **160 s** |
| Scoping a terrain re-emit to the centrelines that cross a zone | the one-compiler invariant makes every blob the union of what the zone keeps | live T8 has 0 m of centreline in (13,11)/(13,12) and seq 1946 carries **878** and **175** modified samples there; a centreline-scoped repair leaves the junction broken |

## 16. VERB OWNERSHIP, AND THE CONSTRUCTIVE VERB AS A SINGLE POINT OF FAILURE

### 16.1 THE VERB-OWNERSHIP TABLE — read it before planning any operation

Three subagents and the orchestrator each got this wrong on 2026-09-19, in
different directions, and the argument was settled by the DLL rather than by
opinion: `strings ValheimRcon.dll` contains `FindObjects`, `DeleteObjects`,
`InvokeConsoleCommand` and `Players` — and nothing else in this table.

| Verb | Owner | What it can do |
|---|---|---|
| `spawn_object`, `data=<entry>` | **World Edit Commands** (mod) | **CREATE — the only constructive verb this toolchain has** |
| `findObjects`, `deleteObjects`, `players`, `consoleCommand` | **ValheimRcon NATIVE** (the socket's own assembly) | destroy, measure, transport |
| `objects_count`, `objects_remove`, `objects_clear`, `zones_generate`, `zones_reset`, `zones_restore` | **Upgrade World** (mod) | destroy, measure |
| `terrain_reset`, `objects_reset` | **NOBODY** — in the harness's own `STAGED_VERBS` and owned by no installed plugin (§16.4) | nothing. They answer `Unknown command` behind an `executed.` receipt |

**The standing consequence, and it is the shape of the whole harness: there is
exactly ONE constructive verb and several destructive ones.** Lose the content
mod that owns it and what remains is a harness that can only subtract — with
every destructive and every measurement path still live, still answering, and
still cheerful.

The scope trap that caused exactly that is worth stating as a sentence, because
the classification looks correct until you ask who is calling: **World Edit
Commands is client-side in how a PLAYER uses it and server-side in how this
harness uses it.**

### 16.2 The absence is silent

MEASURED 2026-09-19, and it is the worst-shaped outage this harness can have.

```
MISSING   spawn_object + data=<entry>      World Edit Commands   <- the ONLY way this
                                                                    toolchain creates anything
LIVE      findObjects, deleteObjects       ValheimRcon native    (destroy + measure)
LIVE      objects_count, objects_remove,
          zones_generate, zones_reset,
          zones_restore                    Upgrade World         (destroy + measure)
ABSENT    terrain_reset, objects_reset     nobody                (never existed, §16.4)
```

**Every destructive and every measurement path stayed live; the single creative
path died.** So in that window the harness could only subtract, and every
two-part operation — all of them are delete-then-place — destroyed its first
half and evaporated its second (§16.3).

Cause `[SAVE]`: the 2026-09-19T04:30:16Z profile scope rebuild flipped
`JereKuusela-World_Edit_Commands` 1.77.0 from `shared` to `client-only` on
`ulfsland-dn`, which is the SERVER profile, and the 11:22:53Z deploy carried that
to the server. `JereKuusela-Server_devcommands` stayed `shared`.

Nothing announced it: every call still answered `Command '<x>' executed.` The
discriminator is one line in the container's own stdout — `Unknown command
'<verb>'` means the verb is ABSENT, `Error: ... not recognized` means it is
PRESENT and rejected your argument — and reading it there rather than in the
reply is §3.5.

**Rule: while the constructive verb is absent, nobody runs the destructive half
of anything** — this project's licence to remove vegetation is "every removal is
reversible by position" (854 records, 7,538 objects, each with prefab, id, xz,
y), and that guarantee is VOID for as long as nothing can place. A record that
claims a reversibility it does not have is worse than no record at all.

**Pre-flight, cheap, and it would have cost nothing:** probe the constructive
verb before taking a write window.

```
consoleCommand spawn_object <a_registered_prefab> pos=0,0,0
  -> container log says `Unknown command`  =>  REFUSE THE WINDOW
```

### 16.3 A two-part operation has two halves and they can fail SEPARATELY

**This destroyed live world content.** Every mutating operation in this harness
is a delete followed by a place, and the two halves are owned by DIFFERENT
packages (§16.1), so one can be live while the other is gone:

```
terrain_write    deleteObjects -zone .. -prefab _TerrainCompiler -force  ValheimRcon NATIVE
            THEN spawn_object _TerrainCompiler .. data=<entry>           World Edit Commands
clearing         objects_remove ...   Upgrade World       THEN place     World Edit Commands
retire-replace   deleteObjects ...    ValheimRcon native  THEN spawn_object
```

MEASURED 2026-09-19: with World Edit Commands gone from the server, the delete
landed and the respawn evaporated. The container log holds the whole event:

```
Deleting 1 objects: -Prefab: _TerrainCompiler Id: 89145:1
Position: (1024 0 704) Zone: (16 11) [DELETED]
```

Zone (16,11)'s `ulfsgard-dredge` compiler was deleted with nothing put back:
**the harbour basin dredge is GONE.** It was a terrain delta rather than a piece,
so no census of standing objects will ever notice its absence.

**Rule: before running the destructive half of a two-part operation, PROVE the
constructive half is available** — the §16.2 probe, in the same write window,
against the same server, read out of the container log and not out of the reply
(§3.5). An operation whose second half is unavailable is not a degraded
operation; it is a deletion.

### 16.4 A verb in the harness's own taxonomy is NOT a verb that exists

MEASURED 2026-09-19: **`terrain_reset` and `objects_reset` are listed in the
harness's own `STAGED_VERBS`, and no installed plugin owns either literal** —
not `UpgradeWorld.dll` 1.82.0, not anything on either cache side. **Any code
path that emits one receives `Unknown command` behind a cheerful
`executed.`** (§3.5). That is tonight's exact failure mode, already armed and
waiting for its first caller.

No emitter for either verb was found, so this is **latent, not active damage** —
which is why it is written down rather than repaired: the next agent to reach
for `terrain_reset` will reach for it because the harness's own table says it is
there.

**It also retro-justifies a refusal.** `terrain_reset` was declined on the ghost
road because it is zone-granular and those five zones carry **212.5 m of live
T4/T8 carriageway** beside the **286 m** decoy, so running it would have taken
the live junction out with the target. It would ALSO simply not have run: an
`executed.` receipt, nothing changed, and no reason to suspect the verb rather
than the geometry. A refusal made for a good reason was protected by a second
one nobody could see.

**Read that the right way round: the geometric reason was sufficient on its
own.** A refusal grounded in measured geometry does not need a second reason to
be correct, and nobody should conclude that refusing on geometry is only safe
when a hidden second reason happens to back it up.

**Rule: probe the surface, do not read the table.** A taxonomy, a config, a
constant list and a docstring are all claims; only a probe against the live
server is evidence.

### 16.5 The four packages this harness cannot run without, and nothing marks them

```
Server_devcommands  1.113.0     ValheimRcon          1.6.2
Upgrade_World       1.82.0      World_Edit_Commands  1.77.0
all four scope=shared on ulfsland-dn as of staging, 2026-09-19
```

**All four are indistinguishable from any other convenience mod until one of
them fails**, and one of them already did (§16.2). **`Upgrade World` is exactly
as exposed as World Edit Commands was**: classify it by how a player uses it and
the harness loses `objects_count`, `objects_remove`, `zones_generate` and
`zones_restore` in one move — every measurement path and every clearing path, on
top of the constructive one.

The root cause is a process defect and it will recur: the scope classifier
reasoned about **how a PLAYER uses a package**. That is correct for
`Infinity_Hammer`, `RuinsMaker`, `PlantEasily` and `Ballista_Infinite_Ammo` —
all four correctly flipped — and catastrophically wrong for the one package that
is a player convenience tool AND this toolchain's only constructive verb. **The
category "client-side tool the server must nonetheless run" did not exist in the
model.** It has to.

**The cheapest guard, and it is one round trip per verb:** probe all four
packages' verbs before any build session, treat `Unknown command` as a HARD
STOP, and include `terrain_reset` / `objects_reset` as KNOWN-ABSENT so the list
is honest rather than aspirational. Prove it in both directions: it must pass
against the live set and it must FAIL when pointed at a verb known to be gone.

**A near-miss worth keeping, because a lesser check would have carried a second
false alarm into the restart:** `Infinity_Hammer` looked like another mis-scope
and is not. `blueprints/to_rcon_plan.py`'s own measured note records that
`hammer_restore` calls `Hammer::Equip()`, which needs a live local player
holding a hammer, and there is no headless path through it; every other
reference is a docstring citing the DLL as a **file-format** reference for
parsing `.blueprint` files. **The harness converts blueprints into
`spawn_object` plans; it never calls that mod.**

## 17. TWO BUILDERS EMITTING ONE SHARED ELEMENT EACH EMIT IT IN FULL

MEASURED 2026-09-19 at Ulfsgard, predicted from source and then confirmed live
with controls that discriminate in BOTH directions — which is what makes it a
measurement rather than a hoped-for number:

```
shared pier column x=1080.5, z 687..709 odd, objects_count r=0.6
  -> 2 at all TWELVE positions            24 wood_floor where 12 belong
control x=1067.0  bay1 OUTER apron, emitted once   -> 1, 1, 1
control x=1094.0  bay2 OUTER apron, emitted once   -> 1, 1, 1
control x=1073.0  open water, no decking           -> 0, 0, 0
```

That probe was one column. **The full standing census over the whole city,
taken afterwards with CUBE probes and its own controls, found the same seam in
three prefabs and 52 duplicate pieces.** The per-prefab breakdown, the controls,
and the reason the probe must be a cube rather than a cylinder are in
`structures-and-settlements.md` §3.6 — that file owns the instrument; this one
owns the protocol rule.

Cause: `settlements/harbour_city.py::boathouse()` gives bay N's EAST apron and
bay N+1's WEST apron the same `deck_strip(x_centre-1, x_centre+1)`. Both sheds
emit it. **Any element on a boundary between two sub-builders is a duplication
candidate.**

Remedy, all three together: pitch adjacent units so shared runs become distinct
tile columns; de-duplicate the shared line explicitly at the seam; then run ONE
global exact-row de-duplication over the whole plan before staging.

### 17.1 The replay consequence, and what it does NOT license

**The projection folds two records at one key to ONE piece.** A
regenerated-and-replayed Ulfsland will hold **52 fewer** of these pieces than
this world holds, and the reconciliation will report 52 **missing**. They are
not missing: the replay is correct about them and the source world is wrong.
Nobody changes the projection to reproduce the duplicates.

**But that is NOT an argument for rebuilding instead of culling, and the earlier
form of this paragraph said it was.** The fold's key is `(prefab, u, v)` and
omits y (§6.3), so the same pass that deletes the 52 duplicates at Ulfsgard also
destroys **130 legitimate pieces at that same site** — 1,004 projected rows
become **828**, `woodwall` 164 -> 68, `wood_pole_log_4` 183 -> 133,
`fb_fortress_portcullis` 6 -> 3. **The recommendation is withdrawn: cull the
duplicates in the live world; do not wait for a rebuild to do it.** A fold is a
repair only where its key is exact.

What that does to every fidelity figure this project quotes — and why a replay
report must separate *"differs because the replay is wrong"* from *"differs
because the source world is wrong"* — is stated once, with its numbers, in
`world-generation-and-replay.md` §5.5.4.
