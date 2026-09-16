#!/usr/bin/env python3
"""The transport must refuse the query that wedges the server, and must never
report a placement it did not confirm.

THE DEFECTS THESE EXIST FOR
---------------------------
Four server wedges on Ulfsland in one night, each one costing a container
restart of 3-7 minutes, and one of them freezing the operator mid-session.
MEASURED cause, from the deployed ValheimRcon 1.6.2 IL: at IL_00e1-IL_0101 of
`RconProxy.<HandleCommandAsync>d__23::MoveNext`,

    Log.Message(Concat("Command completed: ", command, "\\n", result.Text))

runs on the FULL result text, inline on the Unity main thread, and
`RconCommandReceiver.ValidatePayloadLength` only truncates later when the reply
packet is built.  `findObjects`' per-ZDO listing IS `result.Text`, and its usage
string makes every filter OPTIONAL -- so the bare verb writes the whole world
into one main-thread stdout write.  ~1,900 pieces is ~200 KB.

The second defect is quieter and did more damage to trust: a sender that
counted a plan line as placed without checking what came back reported
"1,907 ok" for a run that had placed nothing.  `InvokeConsoleCommand.OnHandle`
returns exactly `Command '<joined>' executed.`, so the echo is available and
free; not comparing it was the bug.

WHY THESE ARE TESTS
-------------------
Both failures are silent at the call site.  An unscoped `findObjects` looks
like a reasonable question right up to the outage, and an unverified send looks
like a success.  These assert the two things a consumer observes: the dangerous
command does not reach the socket, and an unconfirmed batch aborts with the
checkpoint sitting on the last line that really landed.
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

TERRAFORM = Path(__file__).resolve().parent / "jumpstart" / "terraform"
sys.path.insert(0, str(TERRAFORM))

import place as PL  # noqa: E402
import rcon as RC  # noqa: E402


class TheGuardRefusesTheQueryThatWedgedUs(unittest.TestCase):
    def test_bare_findobjects_is_refused(self):
        # The raise IS the contract. This deliberately does NOT assert the
        # message text: the wording moved once already, when the guard learned
        # that a SMALL unscoped box is safe and the requirement became
        # radius-bounded rather than prefab-scoped. A test that pins prose
        # fails on a correct change and teaches the next reader to re-pin it.
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard("findObjects")

    def test_a_small_unscoped_box_is_allowed_and_one_metre_more_is_not(self):
        """The boundary the corridor census actually rides on.

        MEASURED: the per-cell census asks ONE unscoped `findObjects -near cx y
        cz 8` per 16 m cell at five y levels, and that is what took a segment
        from 588 socket calls and 988 echoed log lines down to 107 and 645 for
        an identical ZDO id set.  So the 8 m unscoped form is not a tolerated
        edge case, it is the shape in production, and a guard that refused it
        would push every caller back onto the per-station discs that wedged the
        server.  The pair is the point: allowed at the bound, refused past it.
        """
        RC.guard(f"findObjects -near 100 37 -100 {RC.UNSCOPED_NEAR_MAX_M:g}")
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard(f"findObjects -near 100 37 -100 {RC.UNSCOPED_NEAR_MAX_M + 1:g}")

    def test_prefab_without_radius_is_refused(self):
        """The filter that feels sufficient and is not: a prefab filter alone
        still lists every instance in the WORLD, and `stone_floor_2x2` has 127
        instances on one pad alone."""
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard("findObjects -prefab stone_floor_2x2")

    def test_radius_without_prefab_is_refused(self):
        """A radius alone is what wedged the server: MEASURED, a cube holding
        1,907 pieces."""
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard("findObjects -near -4672 71 -325 40")

    def test_both_scopes_together_are_allowed(self):
        RC.guard("findObjects -prefab portal_wood -near -4672 71 -325 40")

    def test_case_is_not_a_loophole(self):
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard("FINDOBJECTS -PREFAB x")

    def test_logs_is_capped_but_not_banned(self):
        """`logs` defaults to 5 lines (IL_0028 `ldc.i4.5`), so the bare verb is
        harmless; only an explicit large `-lines` is a main-thread write."""
        RC.guard("logs")
        RC.guard(f"logs -lines {RC.LOG_LINES_CAP}")
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard(f"logs -lines {RC.LOG_LINES_CAP + 1}")

    def test_bounded_verbs_are_left_alone(self):
        """Guarding these would have broken jumpstart.py's `globalKeys` read
        for no measured reason: none of them scales with world size."""
        for cmd in ("players", "globalKeys", "adminlist", "banlist",
                    "permitted", "save", "showContainer 123:4"):
            RC.guard(cmd)

    def test_an_oversized_request_is_refused_before_the_socket(self):
        with self.assertRaises(RC.UnboundedQuery):
            RC.guard("consoleCommand " + "x" * RC.MAX_PAYLOAD)


class BatchingStaysInsideBothDirectionsOfTheCap(unittest.TestCase):
    """The REPLY binds, not the request: `Command '<joined>' executed.` is
    `ECHO_OVERHEAD` bytes longer than the joined text, and a truncated reply
    fails the echo comparison and aborts a run."""

    def setUp(self):
        self.budget = PL.batch_budget(PL.DEFAULT_BATCH_BYTES)

    def test_every_batch_reply_fits_the_payload_cap(self):
        lines = [f"spawn_object stone_floor_2x2 pos={i},0,0 rot=0,0,0 from=0,0,0"
                 for i in range(500)]
        for group in PL.batches(lines, self.budget):
            joined = ";".join(group)
            reply = f"Command '{joined}' executed."
            self.assertLessEqual(RC.payload_size(reply), RC.MAX_PAYLOAD)
            self.assertLessEqual(RC.payload_size(RC.BRIDGE + joined), RC.MAX_PAYLOAD)

    def test_batching_neither_drops_nor_reorders_a_line(self):
        """A dropped line is a missing piece nobody notices until the building
        has a hole in it."""
        lines = [f"cmd{i}" for i in range(97)]
        flat = [line for group in PL.batches(lines, 40) for line in group]
        self.assertEqual(flat, lines)

    def test_a_line_longer_than_the_budget_is_emitted_alone(self):
        """It cannot be split, so it must not be silently merged into a batch
        that then exceeds the cap; `guard()` rejects it loudly instead."""
        groups = PL.batches(["short", "x" * 200, "short2"], 40)
        self.assertIn(["x" * 200], groups)

    def test_the_budget_is_clamped_even_when_a_caller_asks_for_more(self):
        self.assertLessEqual(PL.batch_budget(10_000),
                             RC.MAX_PAYLOAD - RC.ECHO_OVERHEAD)


class FakeRcon:
    """Enough of `Rcon` for `send()`: it only calls `command` and `probe`."""

    def __init__(self, *, break_echo_at: int | None = None,
                 stall_at_probe: int | None = None):
        self.sent: list[str] = []
        self.probes = 0
        self.break_echo_at = break_echo_at
        self.stall_at_probe = stall_at_probe

    def command(self, cmd: str, deadline: float | None = None) -> str:
        self.sent.append(cmd)
        if cmd == "save":
            return "Saved"
        joined = cmd[len(RC.BRIDGE):]
        if self.break_echo_at is not None and len(self.sent) == self.break_echo_at:
            return "Command 'something else entirely' executed."
        return f"Command '{joined}' executed."

    def probe(self, deadline: float = 15.0) -> float:
        self.probes += 1
        if self.stall_at_probe is not None and self.probes == self.stall_at_probe:
            raise RC.MainThreadStalled("stalled (test)")
        return 0.033


class AnUnconfirmedBatchAborts(unittest.TestCase):
    def _run(self, rc: FakeRcon, lines: list[str], ckpt: Path, **kw):
        groups = PL.batches(lines, 60)
        return PL.send(rc, groups, start=0, total=len(lines), ckpt=ckpt,
                       plan="p.txt", probe_every=kw.get("probe_every", 0),
                       probe_deadline=15.0, save_every=kw.get("save_every", 0),
                       delay=0.0)

    def test_a_clean_run_confirms_every_line(self):
        lines = [f"cmd{i:03d}" for i in range(50)]
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "c.json"
            sent, rtts, _ = self._run(FakeRcon(), lines, ckpt)
            self.assertEqual(sent, len(lines))
            self.assertEqual(json.loads(ckpt.read_text())["confirmed"], len(lines))
            self.assertEqual(len(rtts), len(PL.batches(lines, 60)))

    def test_a_mismatched_echo_stops_the_run(self):
        lines = [f"cmd{i:03d}" for i in range(50)]
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "c.json"
            with self.assertRaises(PL.Aborted) as caught:
                self._run(FakeRcon(break_echo_at=3), lines, ckpt)
            self.assertIn("NOT CONFIRMED", str(caught.exception))

    def test_the_checkpoint_after_an_abort_is_the_last_line_that_landed(self):
        """The resumability contract: re-send nothing that landed, skip nothing
        that did not."""
        lines = [f"cmd{i:03d}" for i in range(50)]
        groups = PL.batches(lines, 60)
        landed = sum(len(g) for g in groups[:2])
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "c.json"
            with self.assertRaises(PL.Aborted):
                self._run(FakeRcon(break_echo_at=3), lines, ckpt)
            self.assertEqual(json.loads(ckpt.read_text())["confirmed"], landed)

    def test_a_stalled_probe_stops_the_run_and_keeps_the_checkpoint(self):
        lines = [f"cmd{i:03d}" for i in range(200)]
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "c.json"
            with self.assertRaises(RC.MainThreadStalled):
                self._run(FakeRcon(stall_at_probe=1), lines, ckpt, probe_every=2)
            confirmed = json.loads(ckpt.read_text())["confirmed"]
            self.assertGreater(confirmed, 0)
            self.assertLess(confirmed, len(lines))

    def test_the_send_loop_issues_no_query_at_all(self):
        """Placement is safe to batch; verification is the dangerous half. The
        loop must not be tempted into counting what it placed."""
        lines = [f"cmd{i:03d}" for i in range(200)]
        rc = FakeRcon()
        with tempfile.TemporaryDirectory() as tmp:
            self._run(rc, lines, Path(tmp) / "c.json", probe_every=5,
                      save_every=100)
        verbs = {c.split(" ", 1)[0] for c in rc.sent}
        self.assertEqual(verbs, {"consoleCommand", "save"})
        self.assertNotIn("findObjects", " ".join(rc.sent))

    def test_save_bounds_how_many_pieces_a_crash_can_cost(self):
        """The guarantee `--save-every` sells: at no point are more than
        `save_every` plus one batch of pieces unsaved.  Counting batches
        instead of pieces would make that bound depend on line length."""
        lines = [f"cmd{i:03d}" for i in range(200)]
        rc = FakeRcon()
        save_every = 50
        with tempfile.TemporaryDirectory() as tmp:
            self._run(rc, lines, Path(tmp) / "c.json", save_every=save_every)
        biggest_batch = max(len(g) for g in PL.batches(lines, 60))
        unsaved = 0
        worst = 0
        for cmd in rc.sent:
            if cmd == "save":
                unsaved = 0
                continue
            unsaved += cmd[len(RC.BRIDGE):].count(";") + 1
            worst = max(worst, unsaved)
        self.assertGreater(sum(1 for c in rc.sent if c == "save"), 0)
        self.assertLessEqual(worst, save_every + biggest_batch - 1)


class ResumeArithmetic(unittest.TestCase):
    """An off-by-one here duplicates or drops pieces silently."""

    def test_resume_sends_exactly_the_unconfirmed_tail(self):
        lines = [f"cmd{i:03d}" for i in range(50)]
        rc = FakeRcon()
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "c.json"
            ckpt.write_text(json.dumps({"plan": "p.txt", "confirmed": 20,
                                        "total": 50}))
            start = json.loads(ckpt.read_text())["confirmed"]
            groups = PL.batches(lines[start:], 60)
            sent, _, _ = PL.send(rc, groups, start=start, total=len(lines),
                                 ckpt=ckpt, plan="p.txt", probe_every=0,
                                 probe_deadline=15.0, save_every=0, delay=0.0)
            self.assertEqual(sent, 30)
            self.assertEqual(json.loads(ckpt.read_text())["confirmed"], 50)
        wire = ";".join(c[len(RC.BRIDGE):] for c in rc.sent)
        self.assertNotIn("cmd019", wire)
        self.assertIn("cmd020", wire)
        self.assertIn("cmd049", wire)


class TheProbeDecidesOnTheRoundTrip(unittest.TestCase):
    """MEASURED on Ulfsland: the `Connections N ZDOS:` heartbeat printed at
    16:24:24, 16:34:24, 16:44:24, 16:54:24, 17:04:24 and 17:14:24 -- six
    consecutive lines exactly 600 s apart.  A 6 s sample of that log once
    called a healthy server wedged, and a 40 s sample called a wedged server
    healthy.  So liveness here is decided ONLY by whether a bounded RCON
    command comes back: MEASURED from the IL, `HandleCommandAsync` queues
    every command through `ThreadingUtil.RunInMainThread` and awaits it, so a
    reply proves the Unity main thread ran a frame.  The log is read for
    DIAGNOSIS after a stall, never to decide one.
    """

    def setUp(self):
        self.rc = RC.Rcon.__new__(RC.Rcon)   # no socket, no config read
        self.log_reads = 0
        self._real_tail = RC.tail_container_log

        def counting_tail(*a, **kw):
            self.log_reads += 1
            return ["<diagnostic tail>"]

        RC.tail_container_log = counting_tail

    def tearDown(self):
        RC.tail_container_log = self._real_tail

    def test_a_timeout_is_a_stall(self):
        def timing_out(cmd, deadline=None):
            raise socket.timeout("timed out")

        self.rc.command = timing_out
        with self.assertRaises(RC.MainThreadStalled):
            self.rc.probe(deadline=0.01)

    def test_a_healthy_reply_needs_no_log_read_at_all(self):
        self.rc.command = lambda cmd, deadline=None: "Online 0"
        rtt = self.rc.probe(deadline=15.0)
        self.assertGreaterEqual(rtt, 0.0)
        self.assertEqual(self.log_reads, 0)

    def test_a_foreign_reply_is_not_read_as_health(self):
        """A desynchronised stream can hand `players` somebody else's answer;
        counting that as alive is how a wedged server was called healthy."""
        self.rc.command = lambda cmd, deadline=None: "Command 'x' executed."
        with self.assertRaises(RC.MainThreadStalled):
            self.rc.probe(deadline=15.0)


class TheSinkStallIsClassifiedNotGuessed(unittest.TestCase):
    """The other stall, and the one that cost six minutes of blind stdout.

    MEASURED inside the Ulfsland container while RCON answered in 88 ms:
    supervisord in `unix_wait_for_peer` (blocked in `sendto()` to the
    `/dev/log` DGRAM socket) and syslogd in `pipe_write` (blocked writing its
    stdout to the pipe only supervisord drains).  Neither has a timeout, so it
    never clears itself; a 25 s sustained read of that pipe cleared it in 2 s.

    This test exists because the project's recurring bug is a check that
    confidently answers a question it is not measuring.  Reporting a stall
    that is not there REFUSES a build that would have worked; missing one lets
    console output fill the game's stdout pipe until the main thread blocks.
    Both directions are asserted.
    """

    def setUp(self):
        self._real = RC._in_container
        self.calls: list[str] = []

    def tearDown(self):
        RC._in_container = self._real

    def _probe(self, text: str):
        def fake(script, name=RC.CONTAINER, timeout=30.0):
            self.calls.append(script)
            return text
        RC._in_container = fake

    STALLED = ("PIDS 29 30\n"
               "WCHAN unix_wait_for_peer pipe_write\n"
               "READFD /proc/29/fd/7\n")
    HEALTHY = ("PIDS 29 30\n"
               "WCHAN do_poll.constprop.0 __skb_wait_for_more_packets\n"
               "READFD /proc/29/fd/7\n")

    def test_the_measured_signature_is_a_stall(self):
        self._probe(self.STALLED)
        state = RC.log_sink_state()
        self.assertTrue(state["stalled"])
        self.assertEqual(state["read_fds"], ["/proc/29/fd/7"])

    def test_the_normal_event_loops_are_not_a_stall(self):
        self._probe(self.HEALTHY)
        self.assertFalse(RC.log_sink_state()["stalled"])

    def test_one_half_of_the_signature_is_not_a_stall(self):
        """Either wchan alone is a normal momentary state -- syslogd writes to
        that pipe constantly, and supervisord talks to /dev/log constantly."""
        for wchans in ("unix_wait_for_peer do_poll.constprop.0",
                       "do_poll.constprop.0 pipe_write"):
            self._probe(f"PIDS 29 30\nWCHAN {wchans}\nREADFD /proc/29/fd/7\n")
            self.assertFalse(RC.log_sink_state()["stalled"], wchans)

    def test_the_signature_is_order_sensitive(self):
        """supervisord is reported first.  syslogd blocked in a unix send and
        supervisord blocked in a pipe write is a DIFFERENT fault, and treating
        it as this one would drain the wrong pipe."""
        self._probe("PIDS 29 30\nWCHAN pipe_write unix_wait_for_peer\n"
                    "READFD /proc/29/fd/7\n")
        self.assertFalse(RC.log_sink_state()["stalled"])

    def test_a_container_without_those_processes_is_not_a_stall(self):
        self._probe("MISSING  \n")
        state = RC.log_sink_state()
        self.assertFalse(state["stalled"])
        self.assertEqual(state["read_fds"], [])

    def test_recovery_on_a_healthy_sink_drains_nothing(self):
        """A drain steals bytes from supervisord, so it must never run
        speculatively."""
        self._probe(self.HEALTHY)
        self.assertFalse(RC.recover_log_sink()["stalled"])
        self.assertTrue(all("dd if=" not in c for c in self.calls))

    def test_recovery_refuses_when_it_cannot_find_the_pipe(self):
        """Better to report than to drain a guessed fd."""
        self._probe("PIDS 29 30\nWCHAN unix_wait_for_peer pipe_write\n")
        with self.assertRaises(RuntimeError):
            RC.recover_log_sink()

    def test_recovery_drains_the_discovered_fd(self):
        self._probe(self.STALLED)
        RC.recover_log_sink(seconds=1.0)
        self.assertTrue(any("dd if=/proc/29/fd/7" in c for c in self.calls))


if __name__ == "__main__":
    unittest.main()
