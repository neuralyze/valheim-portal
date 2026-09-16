#!/usr/bin/env python3
"""Can this body actually be SPAWNED? Asked of the thing that does the spawning.

WHY THIS EXISTS, MEASURED: `library/data/base_audit.json` reports
`missing_prefabs: []` for `hs_blackforest_crimsonchaostownhall.blueprint`, and
`blueprints/to_rcon_plan.py` REFUSES to emit a plan for that same body --
"Placeable_HardRock: not in the evidence file". The two are asking different
questions against different evidence, and only one of them is the thing that
puts objects in the world. So the spawnability gate is a real run of
`to_rcon_plan.py`, and the audit field is treated as a hint.

That is the same defect shape as everything else this project has paid for: a
check that confidently answers a question it is not measuring. The audit is not
wrong about what it measured; it was being asked to stand in for a different
measurement.

Usage:
    ./spawnable.py hs_meadows_cottage.blueprint [more...]
    ./spawnable.py --plan-json plan.json      # every body the plan uses
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent

MISSING_RE = re.compile(r"^\s{2}(\S+): (.+)$", re.M)


def _resolver():
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(name: str, at=(0.0, 0.0, 0.0), yaw: float = 0.0) -> dict:
    """Run the real plan emitter and report what it says.

    A body passes only if a plan comes out with NO dropped prefab. `missing`
    lists what the emitter refused on, which is the list an operator would have
    to pass `--drop-prefab` for -- and a body that needs that is a worse choice
    than one that does not.
    """
    libmat = _resolver()
    body = libmat.locate_by_filename(name)
    if body is None:
        return {"name": name, "ok": False, "missing": [],
                "error": "no body resolves for this name"}
    out = Path("/tmp/settle/spawnable") / f"{Path(name).stem}.plan"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(JUMPSTART / "blueprints/to_rcon_plan.py"), str(body),
         "--at", f"{at[0]}", f"{at[1]}", f"{at[2]}", "--rotate", f"{yaw}",
         "--align", "floor-center", "--out", str(out)],
        capture_output=True, text=True)
    text = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
    missing = []
    if "cannot be spawned" in text:
        for prefab, why in MISSING_RE.findall(text.split("cannot be spawned")[1]):
            missing.append({"prefab": prefab, "why": why.strip()})
    lines = len([l for l in out.read_text().splitlines() if l.strip()]) if ok else 0
    return {"name": name, "ok": ok and not missing, "missing": missing,
            "commands": lines, "stderr_tail": text[-400:] if not ok else ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bodies", nargs="*")
    ap.add_argument("--plan-json")
    a = ap.parse_args()
    names = list(a.bodies)
    if a.plan_json:
        names += sorted(json.loads(Path(a.plan_json).read_text())["bodies"])
    bad = 0
    for n in dict.fromkeys(names):
        r = check(n)
        flag = "OK  " if r["ok"] else "FAIL"
        extra = ""
        if r["missing"]:
            extra = " missing=" + ",".join(m["prefab"] for m in r["missing"])
        elif not r["ok"]:
            extra = " " + r.get("error", r.get("stderr_tail", ""))[:160].replace("\n", " ")
        print(f"{flag} {n[:56]:58s} commands={r['commands']:5d}{extra}")
        bad += 0 if r["ok"] else 1
    print(f"\n{bad} of {len(dict.fromkeys(names))} bodies cannot be spawned as-is")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
