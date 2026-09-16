#!/usr/bin/env python3
"""The records that have to exist before SettleBuild's first live write.

Five things, and each is a fact the operator or the next agent cannot get from
anywhere else: the world backup, the chain fork, the machine-readable
`chain_reset` that documents it, the repair that made appends possible again,
and the cross-branch re-verification of the two guards the fork made inert.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "terraform"))
from live import LiveBuilder  # noqa: E402

EXPECTED_PREV = ("ad40153c9b7a29683d5d0c5006896e6387097e36e690750098c580e5c4"
                 "c5a30f")
ACTUAL_PREV = ("75bc3d735da240706974ff5536fca86ba7ebf987c536986b11fdfe71a706"
               "50f7")


def main() -> int:
    with LiveBuilder(actor="SettleBuild") as b:
        b.note(
            "WORLD BACKED UP BEFORE ANY SETTLEMENT WRITE: "
            "/media/big4/projects/game/valheim/world_backups/"
            "world-Ulfsland-backup-2026-09-15_21-47-47.tgz, taken with "
            "hostops/backup_valheim_world.sh Ulfsland. 16 settlements, 50 "
            "buildings, 19,203 pieces and 33 pads are about to be written "
            "through this ledger and nothing else.",
            role="site_pad", site_id="stenvik")
        b.emit("chain_reset", params={
            "broken_at_line": 47,
            "expected_prev": EXPECTED_PREV,
            "actual_prev": ACTUAL_PREV,
            "duplicate_seqs": {"43": [43, 47], "44": [44, 48],
                               "45": [45, 49], "46": [46, 50]},
            "seq_advisory_from_line": 47,
            "branches": [
                {"branch": "A", "lines": [43, 44, 45, 46], "actor": "RoadNet",
                 "ts": "2026-09-16T03:02:52Z",
                 "ops": ["note", "observe", "note", "note"],
                 "world_effect": "NONE -- all four are record-only and sent "
                                 "nothing to the console"},
                {"branch": "B", "lines": [47, 48, 49, 50],
                 "actor": "Crossings", "ts": "2026-09-16T03:04:18Z",
                 "ops": ["spawn", "spawn", "portal", "spawn"],
                 "world_effect": "ALL FOUR ARE IN THE WORLD and their "
                                 "postconditions passed: ferry-terminal-"
                                 "eastisle's sign post, sign, portal "
                                 "x-ferry-e and a Longship. Nobody re-places "
                                 "that boat."}],
            "cause": "Ledger._append_raw read the chain head, computed prev "
                     "and wrote, with no lock and with the head CACHED at "
                     "open: a lost-update race between two long-lived "
                     "builders. The console token could never have prevented "
                     "it, because note and observe send nothing to the "
                     "console and took no token. Fixed under this record's "
                     "own write path: an exclusive flock across "
                     "read-head-then-write, with the head re-read from the "
                     "file inside the critical section.",
            "text": "seq is ADVISORY from file line 47 onward -- 43-46 each "
                    "appear twice. Replay, resume progress and merged_from "
                    "must key on FILE POSITION, the only total order this "
                    "artefact has. Nothing was renumbered and no prev was "
                    "rewritten: a log edited to look clean is worth less "
                    "than one carrying a labelled wart.",
            "role": "site_pad", "site_id": "stenvik"},
            meta={"verified_against_file": True,
                  "tool": "tools/jumpstart/ledger/schema.py::scan",
                  "param_names": "broken_at_line / expected_prev / "
                                 "actual_prev, the names BuildLedger's "
                                 "replay-side reader verifies against the "
                                 "file so a reset cannot launder a fork it "
                                 "does not accurately describe"})
        b.note(
            "THE REPAIR, authorised by Main, in ledger/writer.py and "
            "ledger/schema.py only. (1) schema.scan walks the log in FILE "
            "ORDER and reports forks AS DATA; chain_head no longer raises on "
            "a prev that matches an EARLIER line's digest, and still raises "
            "on a prev that matches no line at all -- that is an edit, not a "
            "race. An integrity check whose only failure mode is total "
            "refusal cannot record its own finding, which is why a damaged "
            "log could not be annotated. Strictness for REPLAY stays in "
            "replay.py, which is BuildLedger's file: opening to append and "
            "opening to replay are different questions, and one function was "
            "answering both with the stricter answer. (2) _append_raw holds "
            "an exclusive flock across read-head-then-write and RE-READS the "
            "head from the file inside the critical section -- the re-read is "
            "the decisive half, because a lock with a cached head reproduces "
            "this fork exactly. _cross_record_checks runs inside the lock, "
            "deliberately: both guards read prior records, so the window "
            "between the read and the write is exactly where they go inert. "
            "The lock is never held across a console round trip, it carries a "
            "30 s timeout, and it names its holder so a crash reads as 'held "
            "by pid N since T' rather than as a hang. close_report now "
            "reports every fork and every duplicate seq as a problem, and "
            "reports portal tag ends by FILE LINE as well as by seq, so an "
            "ambiguous label cannot be the only thing a reader is given.",
            role="site_pad", site_id="stenvik")
        b.observe(
            "zone ownership and portal tags across BOTH fork branches",
            "MEASURED by walking all 51 lines in FILE ORDER, which is the "
            "only total order this artefact has, rather than stopping at the "
            "first branch as every reading guard did while the fork was "
            "undocumented. Re-run before any further terrain_write or portal.",
            "tools/jumpstart/settlements/preflight_zones.py",
            {"ledger_lines": 51,
             "zones_with_a_compiler_claim": [[-18, 22], [-18, 26]],
             "zones_flatten_forbidden": [[-5, -2], [-5, -1], [-5, 4], [0, -4],
                                         [8, 17], [27, 10], [30, 9], [31, 9]],
             "portal_tags_in_log": {"x-harbour": [29], "x-ferry-e": [49]},
             "zones_settlements_will_write": 46,
             "clash_with_compiler_claim": 0,
             "clash_with_flatten_forbidden": 0,
             "witness_pads_untouched": True,
             "note": "the two compiler claims are GroundTruth's witness pads "
                     "at (-1152, 1408) and (-1152, 1664); none of the 46 "
                     "settlement zones touches either, and the operator has "
                     "to inspect them for floating grass"},
            role="site_pad", site_id="stenvik")
        print(json.dumps(b.close(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
