#!/usr/bin/env python3
"""The records that have to exist before SettleBuild's first live write.

Four things, and each is a fact the operator or the next agent cannot get from
anywhere else: the world backup, the chain fork, the repair that made appends
possible again, and the cross-branch re-verification of the two guards the fork
made inert.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "terraform"))
from live import LiveBuilder  # noqa: E402


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
        b.note(
            "CHAIN FORK, documented rather than repaired away. File line 47 "
            "(seq 43, Crossings spawn, 03:04:18Z) declares prev=ad40153c but "
            "the preceding line digests to 75bc3d73, and seqs 43-46 appear "
            "TWICE: RoadNet's four record-only ops (note/observe/note/note at "
            "03:02:52, which sent NOTHING to the world) on one branch, and "
            "Crossings' spawn/spawn/portal/spawn for ferry-terminal-eastisle "
            "(03:04:18-03:04:30, all four verified IN the world, including "
            "portal x-ferry-e and a Longship) on the other. CAUSE: "
            "Ledger._append_raw read the chain head, computed prev and wrote, "
            "with no lock AND with the head cached at open -- a lost-update "
            "race between two long-lived builders. The console token could "
            "never have prevented it, because note and observe take no token. "
            "CONSEQUENCE, which is the worst part and is not the replay: for "
            "the window the fork was open, the two guards that decide by "
            "READING PRIOR RECORDS -- the two-ends-per-tag portal check and "
            "the one-compiler-per-zone clobber check -- were silently INERT, "
            "because a reader that stops at the first branch cannot see claims "
            "made on the second. seq is ADVISORY from file line 47 onward; "
            "replay and merged_from must key on FILE POSITION. Nothing was "
            "renumbered and no prev was rewritten: a log edited to look clean "
            "is worth less than one carrying a labelled wart.",
            role="site_pad", site_id="stenvik")
        b.note(
            "THE REPAIR, authorised by Main, in ledger/writer.py and "
            "ledger/schema.py only. (1) schema.scan walks the log in FILE "
            "ORDER and reports forks AS DATA; chain_head no longer raises on a "
            "prev that matches an EARLIER line's digest, and still raises on a "
            "prev that matches no line at all -- that is an edit, not a race. "
            "An integrity check whose only failure mode is total refusal "
            "cannot record its own finding, which is why a damaged log could "
            "not be annotated. Strictness for REPLAY stays in replay.py, which "
            "is BuildLedger's file: opening to append and opening to replay "
            "are different questions and one function was answering both with "
            "the stricter answer. (2) _append_raw now holds an exclusive flock "
            "across read-head-then-write and RE-READS the head from the file "
            "inside the critical section -- the re-read is the decisive half, "
            "because a lock with a cached head reproduces this fork exactly. "
            "_cross_record_checks runs inside the lock, deliberately: both "
            "guards read prior records, so the window between the read and the "
            "write is exactly where they go inert. The lock is never held "
            "across a console round trip. It carries a 30 s timeout and names "
            "its holder, so a crashed holder reads as 'held by pid N since T' "
            "rather than as a hang. close_report now reports every fork and "
            "every duplicate seq as a problem, so the wart cannot pass for "
            "clean.",
            role="site_pad", site_id="stenvik")
        b.observe(
            "zone ownership and portal tags across BOTH fork branches",
            "MEASURED by walking all 51 lines in FILE ORDER, which is the only "
            "total order this artefact has, rather than stopping at the first "
            "branch as every reading guard did while the fork was "
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
