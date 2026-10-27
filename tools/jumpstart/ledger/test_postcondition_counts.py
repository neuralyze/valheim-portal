"""Constructed log/query regressions; no server, Docker, or world evidence."""
from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ledger import replay
import console
import rcon


def _command(ident, x, z, radius, ignore="_*"):
    return replay.Server._count_command(ident, x, z, radius, ignore)


def _block(cmd, total, *, rows=None):
    ident = cmd.split("id=", 1)[1].split()[0]
    if rows is None:
        rows = [f"{ident}: {total}"] if total else []
    return ([f"Console: Total: {total}"]
            + [f"Console: {row}" for row in rows]
            + ["[Message:Valheim Rcon] Command completed: consoleCommand",
               f"Command '{cmd}' executed.",
               "Console: [Message:Valheim Rcon] Command completed: consoleCommand",
               f"Command '{cmd}' executed."])


class _SerialServer(replay.Server):
    def count_many(self, queries):
        return [self.count(*query) for query in queries]


@contextlib.contextmanager
def _session(render, *, stale=(), fresh=True, serial=False, socket_reply=None):
    """Only the socket and log-file boundaries are replaced, not their consumers."""
    class Transport:
        def __init__(self):
            self.pending = []
            self.commands = []
            self.lines = list(stale)
            self.reads = 0
            self.marks = 0
            self.sleeps = []
            self.in_flight = False

        def console(self, cmd):
            if self.in_flight:
                raise AssertionError("more than one RCON request in flight")
            self.in_flight = True
            try:
                self.commands.append(cmd)
                self.pending.append(cmd)
                return (socket_reply if socket_reply is not None else
                        f"Command '{cmd}' executed.")
            finally:
                self.in_flight = False

        def mark(self):
            self.marks += 1
            return ("constructed.log", len(self.lines)) if fresh else None

        def read(self, _path=None, offset=0, **_kwargs):
            self.reads += 1
            if self.pending:
                self.lines.extend(render(self.pending))
                self.pending = []
            return self.lines[offset:]

    transport = Transport()
    server = _SerialServer() if serial else replay.Server()
    server.rc = transport
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(console, "sup_log_size", transport.mark))
        stack.enter_context(patch.object(console, "sup_log_since", transport.read))
        stack.enter_context(patch.object(console, "logs_window", transport.read))
        stack.enter_context(patch.object(console.time, "sleep", transport.sleeps.append))
        stack.enter_context(patch.object(rcon, "keep_sink_clear", return_value={}))
        stack.enter_context(patch.object(console.subprocess, "run", side_effect=
                                        AssertionError("real subprocess forbidden")))
        yield server, transport


class PostconditionCountTests(unittest.TestCase):
    def test_together_out_of_order_and_stale_unrelated_output(self):
        queries = [("_ZoneCtrl", 64.0 * i, 0.0, 1.0, "") for i in range(3)]
        cmds = [_command(*query) for query in queries]
        values = dict(zip(cmds, (1, 0, 2)))
        stale = _block(cmds[0], 99)
        unrelated = _command("wood", 999.0, 999.0, 1.0)

        def render(pending):
            lines = _block(unrelated, 77)
            for cmd in reversed(pending):
                lines.extend(_block(cmd, values[cmd]))
            return lines

        with _session(render, stale=stale) as (srv, io):
            self.assertEqual(srv.count_many(queries),
                             [(1, {"_ZoneCtrl": 1}), (0, {}),
                              (2, {"_ZoneCtrl": 2})])
            self.assertEqual(io.commands, cmds)
            self.assertEqual((io.marks, io.reads, len(io.sleeps)), (1, 1, 1))

    def test_stale_success_cannot_replace_a_missing_current_zone_reply(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]
        stale = _block(_command(*queries[1]), 1)
        rec = {"params": {"zones": [[0, 0], [1, 0]]},
               "expect": {"zone_ctrl_exact": {"count": 1, "probe_radius_m": 1.0}}}
        with _session(lambda pending: _block(pending[0], 1),
                      stale=stale) as (srv, _io):
            with self.assertRaises(console.OutputNotFound):
                replay.check_expect(srv, rec)

    def test_missing_or_truncated_one_reply_is_unknown_not_zero(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(3)]
        for damage in ("missing", "echo", "total", "row", "wrong_row",
                       "missing_row", "different_total"):
            with self.subTest(damage=damage):
                def render(pending):
                    lines = []
                    for i, cmd in enumerate(pending):
                        block = _block(cmd, 1)
                        if i == 1:
                            if damage == "missing":
                                block = []
                            elif damage == "echo":
                                block = [ln.replace("executed.", "execu")
                                         for ln in block]
                            elif damage == "total":
                                block[0] = "Console: Total:"
                            elif damage == "row":
                                block[1] = "Console: _ZoneCtrl:"
                            elif damage == "wrong_row":
                                block[1] = "Console: _TerrainCompiler: 1"
                            elif damage == "missing_row":
                                del block[1]
                            else:
                                block[0] = "Console: Total: 2"
                        lines.extend(block)
                    return lines

                with _session(render) as (srv, _io):
                    with self.assertRaises(console.OutputNotFound):
                        srv.count_many(queries)

    def test_duplicate_fresh_command_clusters_are_ambiguous(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]

        def render(pending):
            return (_block(pending[0], 8) + _block(pending[1], 1)
                    + _block(pending[0], 1))

        with _session(render) as (srv, _io):
            with self.assertRaises(console.OutputNotFound):
                srv.count_many(queries)

    def test_lagging_complete_log_is_reread_without_resending_queries(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]
        cmds = [_command(*query) for query in queries]
        reads = [0]

        with _session(lambda pending: []) as (srv, io):
            def delayed(_path, _offset, **_kwargs):
                reads[0] += 1
                return (_block(cmds[0], 1) if reads[0] == 1 else
                        _block(cmds[1], 0) + _block(cmds[0], 1))

            with patch.object(console, "sup_log_since", delayed):
                self.assertEqual(srv.count_many(queries),
                                 [(1, {"_ZoneCtrl": 1}), (0, {})])
            self.assertEqual(io.commands, cmds)
            self.assertEqual(io.sleeps, [2.0, 1.0])

    def test_exact_per_zone_results_and_radius_refusals_match_serial(self):
        zones = [[-2, 3], [0, 0], [4, -5]]
        for expect in ({"zone_ctrl": True},
                       {"zone_ctrl_exact": {"count": 1, "probe_radius_m": 31.0}},
                       {"zone_ctrl_exact": {"count": 1, "probe_radius_m": 32.0}}):
            rec = {"params": {"zones": zones}, "expect": expect}
            values = {(-128.0, 192.0): 1, (0.0, 0.0): 2,
                      (256.0, -320.0): 0}

            def render(pending):
                lines = []
                for cmd in reversed(pending):
                    pos = cmd.split("pos=", 1)[1].split()[0]
                    point = tuple(float(v) for v in pos.split(","))
                    lines.extend(_block(cmd, values[point]))
                return lines

            with self.subTest(expect=expect):
                with _session(render, serial=True) as (srv, old_io):
                    old = replay.check_expect(srv, rec)
                with _session(render) as (srv, new_io):
                    new = replay.check_expect(srv, rec)
                self.assertEqual(new, old)
                self.assertFalse(new[0]["ok"])
                if "zone_ctrl" in expect:
                    self.assertEqual(new[0]["ungenerated"], [[4, -5]])
                elif expect["zone_ctrl_exact"]["probe_radius_m"] == 31.0:
                    self.assertEqual(new[0]["got"], {"-2,3": 1, "0,0": 2, "4,-5": 0})
                else:
                    self.assertEqual(new_io.commands, [])
                self.assertLessEqual(new_io.reads, old_io.reads)

    def test_success_and_duplicate_control_object_keep_distinct_zone_semantics(self):
        for values in ((1, 1), (1, 2)):
            for check in ("zone_ctrl", "zone_ctrl_exact"):
                expect = (True if check == "zone_ctrl" else
                          {"count": 1, "probe_radius_m": 31.0})
                rec = {"params": {"zones": [[0, 0], [1, 0]]},
                       "expect": {check: expect}}

                def render(pending):
                    return [ln for cmd in reversed(pending) for ln in
                            _block(cmd, values[int("pos=64.00," in cmd)])]

                with self.subTest(values=values, check=check):
                    with _session(render, serial=True) as (srv, _io):
                        old = replay.check_expect(srv, rec)
                    with _session(render) as (srv, _io):
                        new = replay.check_expect(srv, rec)
                    self.assertEqual(new, old)
                    self.assertEqual(new[0]["ok"],
                                     check == "zone_ctrl" or values == (1, 1))

    def test_other_independent_count_checks_keep_results_and_filters(self):
        rec = {"params": {"entries": [
                    {"centre": [0.0, 0.0], "zone": [0, 0]},
                    {"centre": [64.0, 0.0], "zone": [1, 0]}]},
               "expect": {
                   "prefab_count": [
                       {"prefab": "_ZoneCtrl", "pos": [0, 0], "max": 1, "count": 1},
                       {"prefab": "wood", "pos": [0, 0], "max": 2,
                        "count": 2, "tolerance": 1}],
                   "absent": [
                       {"prefab": "stone", "pos": [0, 0], "max": 2},
                       {"prefab": "wood", "pos": [64, 0], "max": 2}],
                   "terrain_compiler": True}}

        def render(pending):
            lines = []
            for cmd in reversed(pending):
                lines.extend(_block(cmd, 0 if "id=stone " in cmd else 1))
            return lines

        with _session(render, serial=True) as (srv, old_io):
            old = replay.check_expect(srv, rec)
        with _session(render) as (srv, new_io):
            new = replay.check_expect(srv, rec)
        self.assertEqual(new, old)
        self.assertEqual([c["ok"] for c in new], [True, True, True, False, True, True])
        self.assertEqual(new_io.commands, old_io.commands)
        self.assertTrue(all("ignore=" not in cmd for cmd in new_io.commands
                            if "id=_" in cmd))
        self.assertEqual(new_io.reads, 3)
        self.assertEqual(old_io.reads, 6)

    def test_duplicate_queries_are_measured_separately_not_cached(self):
        query = ("_ZoneCtrl", 0.0, 0.0, 1.0, "")
        values = iter((1, 2))
        with _session(lambda pending: _block(pending[0], next(values))) as (srv, io):
            self.assertEqual(srv.count_many([query, query]),
                             [(1, {"_ZoneCtrl": 1}), (2, {"_ZoneCtrl": 2})])
            self.assertEqual(len(io.commands), 2)

    def test_batch_size_is_bounded_without_dropping_queries(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(10)]
        with _session(lambda pending: [ln for cmd in reversed(pending)
                                       for ln in _block(cmd, 1)]) as (srv, io):
            self.assertEqual(srv.count_many(queries), [(1, {"_ZoneCtrl": 1})] * 10)
            self.assertEqual((len(io.commands), io.marks, io.reads), (10, 2, 2))

    def test_no_file_cursor_keeps_individual_attribution(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]
        with _session(lambda pending: _block(pending[0], 1), fresh=False) as (srv, io):
            self.assertEqual(srv.count_many(queries), [(1, {"_ZoneCtrl": 1})] * 2)
            self.assertEqual(io.reads, 2)

    def test_lost_file_cursor_refuses_instead_of_reading_rotated_history(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]
        reader = console.sup_log_since
        with _session(lambda pending: []) as (srv, _io):
            with patch.object(console, "sup_log_since", reader), \
                    patch.object(console, "_exec", return_value=console._COUNT_LOG_ROTATED):
                with self.assertRaises(console.OutputNotFound):
                    srv.count_many(queries)

    def test_dry_run_retains_unknown_counts_and_sends_nothing(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]
        with _session(lambda pending: []) as (srv, io):
            srv.dry = True
            self.assertEqual(srv.count_many(queries), [(-1, {}), (-1, {})])
            self.assertEqual(io.commands, [])
            self.assertEqual(io.reads, 0)

    def test_bounded_star_guard_still_refuses_before_sending(self):
        queries = [("_ZoneCtrl", 0.0, 0.0, 1.0, ""),
                   ("*", 0.0, 0.0, 41.0, "_*")]
        with _session(lambda pending: []) as (srv, io):
            with self.assertRaises(replay.LedgerError):
                srv.count_many(queries)
            self.assertEqual(io.commands, [])

    def test_wildcard_tables_keep_single_query_semantics(self):
        queries = [("*", 0.0, 0.0, 2.0, "_*"),
                   ("*", 64.0, 0.0, 2.0, "_*")]
        with _session(lambda pending: _block(pending[0], 3,
                      rows=["wood: 1", "stone: 2"])) as (srv, io):
            self.assertEqual(srv.count_many(queries),
                             [(3, {"wood": 1, "stone": 2})] * 2)
            self.assertEqual(io.reads, 2)

    def test_unbatched_missing_total_is_also_never_zero(self):
        queries = [("*", 0.0, 0.0, 2.0, "_*"), ("*", 64.0, 0.0, 2.0, "_*")]
        with _session(lambda pending: [f"Command '{pending[0]}' executed."]) as (srv, _io):
            with self.assertRaises(replay.LedgerError):
                srv.count_many(queries)

    def test_game_refusal_and_socket_mismatch_remain_errors(self):
        queries = [("_ZoneCtrl", float(i * 64), 0.0, 1.0, "") for i in range(2)]

        def refused(pending):
            return [ln for cmd in pending for ln in
                    ["Console: 'objects_count' is not a recognized command.",
                     f"Command '{cmd}' executed."]]

        with _session(refused) as (srv, _io):
            with self.assertRaises(console.UnrecognizedVerb):
                srv.count_many(queries)
        with _session(lambda pending: [], socket_reply="wrong echo") as (srv, _io):
            with self.assertRaises(console.OutputNotFound):
                srv.count_many(queries)


if __name__ == "__main__":
    unittest.main()
