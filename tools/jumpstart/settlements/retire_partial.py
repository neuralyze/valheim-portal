#!/usr/bin/env python3
"""Demolish a PARTIALLY placed body, through the ledger, and prove it is gone.

A half-placed building is the one state the operator should never find. This
happens when a `spawn_plan`'s wire aborts part way -- tonight, on the first
batch, because the container log truncates at ~150 characters and the old
log-grep transport could not confirm a 3,600-character batch that had in fact
run. The reply was about the STREAM, not the world, so the honest response is
to count what landed and retire exactly that.

Usage:
    ./retire_partial.py --site stenvik --unit stenvik-hall-1 --retires 67
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv_backup = list(sys.argv)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ledger"))
sys.path.insert(0, str(HERE.parent / "terraform"))
sys.path.insert(0, str(HERE.parent / "blueprints"))

RETIRE_RADIUS_M = 20.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--retires", type=int, action="append", required=True,
                    help="seq number(s) this demolition removes")
    a = ap.parse_args()

    sys.argv = [sys.argv[0]]
    import build as B  # noqa: E402  (argparse-free import)
    import verify_placement as VP  # noqa: E402
    from live import LiveBuilder  # noqa: E402

    plan = json.loads((HERE / "plan.json").read_text())
    unit = next(u for u in B.unit_records(plan, a.site) if u["id"] == a.unit)
    rows = VP.plan_rows(B.SCRATCH / a.site / f"{a.unit}.plan")

    with LiveBuilder(actor="SettleBuild") as b:
        # WHAT IS ACTUALLY THERE, per prefab. Not what the plan says and not
        # what the batching implies: the count is the only honest input to a
        # demolition, because `deleteObjects` echoes one line per object and a
        # reply that looks empty is indistinguishable from a truncated one.
        got, per = b.srv.count("*", unit["x"], unit["z"], RETIRE_RADIUS_M)
        standing = {p: n for p, n in per.items()
                    if p in {r[0] for r in rows}}
        print(f"standing within {RETIRE_RADIUS_M:g} m of "
              f"({unit['x']}, {unit['z']}): total {got}, of this body "
              f"{standing}")
        if not standing:
            print("nothing of this body is standing; no retire to record")
            return 0
        for prefab, count in sorted(standing.items()):
            wire = (f"deleteObjects -prefab {prefab} -near {unit['x']:.2f} "
                    f"{unit['target_y']:.2f} {unit['z']:.2f} "
                    f"{RETIRE_RADIUS_M:.2f} -force")
            res = b.emit("retire", params={
                "prefab": prefab,
                "pos": [unit["x"], unit["target_y"], unit["z"]],
                "radius_m": RETIRE_RADIUS_M,
                "retires": list(a.retires),
                "reason": (
                    f"{count} {prefab} landed from a spawn_plan whose wire "
                    f"aborted on its first batch: the old transport verified a "
                    f"batched command by grepping the container log, and "
                    f"MEASURED the log truncates lines at ~150 characters "
                    f"while the batch is ~3,600, so a placement that HAD run "
                    f"read as a failure. The body is re-placed whole after "
                    f"this demolition. A half-placed building is the one state "
                    f"the operator should never find."),
                "role": "town_body", "site_id": a.unit},
                wire=[wire],
                requires={"mods": ["WorldEditCommands"], "prefabs": [prefab],
                          "blobs": []},
                expect={"absent": [{"prefab": prefab,
                                    "pos": [unit["x"], unit["z"]],
                                    "max": RETIRE_RADIUS_M}]},
                meta={"settlement": a.site, "counted_before": count,
                      "why": ("radius 20 m is the cap: deleteObjects echoes "
                              "155 bytes PER DELETED OBJECT (MEASURED), so a "
                              "wide cube is a question whose answer is long. "
                              "Centred on the pad, not grazing it: MEASURED, "
                              "an object at exactly 20.0 m survived a 20.0 m "
                              "delete.")})
            print(f"  retire {prefab} x{count} -> {res['status']} "
                  f"seq {res['seq']} {res['checks']}")
        got_after, per_after = b.srv.count("*", unit["x"], unit["z"],
                                           RETIRE_RADIUS_M)
        print(f"after: total {got_after} {per_after}")
        print(json.dumps(b.close(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
