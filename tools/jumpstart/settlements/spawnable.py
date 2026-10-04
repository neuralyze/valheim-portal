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

AND THIS FILE COMMITTED THE SAME DEFECT ONE LAYER DOWN. It reported the
emitter's verdict as `ok` plus `missing`, and `missing` is populated ONLY by
the prefab-evidence refusal. MEASURED on the 18 bodies `plan.py` selects:
thirteen refused with `missing == []`, because their refusal was a GEOMETRY
PREFLIGHT -- inverted panels, an unsupported storey, rows buried under the
pad -- and the only trace of it was 400 characters of `stderr_tail` that no
caller read. `plan.py` then printed that empty list as the evidence for
"cannot be spawned without --drop-prefab", naming a remedy belonging to a
different refusal entirely.

So a report now carries THE REASON THE EMITTER GAVE:

  resolved   did the blueprint library resolve this name at all
  missing    prefabs MEASURED absent from the full evidence
  refusals   the preflight's own code, numbers and override flag
  repaired   what the deterministic repairs did, per repair
  reason     one line naming the measured cause and the remedy that fits it

AND IT RUNS THE EMITTER WITH THE EMITTER'S OWN NAMED REPAIRS - upright
panels, seat unsupported bodies, drop sub-grade rows. A gate that asks the
emitter whether it can build a body while withholding the repairs the emitter
itself applies is asking about a configuration nobody would ever run.
MEASURED over the 18: 15 refuse without the repairs, 3 with them. Since
2026-09-24 the emitter applies all three BY DEFAULT (the orchestrated build
passed none of them, and 31 of Fresh42's 43 units refused), so every repair
is passed EXPLICITLY either way - asked for, or switched off with its
`--no-` form - and `repairs` in a report is exactly the set that ran.

Usage:
    ./spawnable.py hs_meadows_cottage.blueprint [more...]
    ./spawnable.py --plan-json plan.json      # every body the plan uses
    ./spawnable.py --no-repairs ...           # the emitter with nothing applied
    ./spawnable.py --selftest
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
# `to_rcon_plan.py` prints one line per refusal, and a REFUSED line names the
# flag that overrides it -- so the remedy in a report is the remedy the
# emitter named for THAT refusal, never one picked from a list.
REFUSED_RE = re.compile(
    r"^\s*preflight REFUSED \(override (\S+)\)\s+(\w+): (.*)$", re.M)
REPAIRED_RE = re.compile(r"^\s*preflight REPAIRED (.+)$", re.M)

# The emitter's own deterministic repairs, in the emitter's own words.
# Named here once so a caller cannot half-apply them: `--no-repairs` switches
# off EVERY one, which it cannot do for a repair this list does not name.
REPAIRS = ("--drop-co-located-copies", "--reorient-upside-down-panels",
           "--upright-panels", "--seat-unsupported-bodies",
           "--seat-sunk-props", "--drop-subgrade-rows",
           "--reseat-blocked-beds")


def _resolver():
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reason(rep: dict) -> str:
    """The MEASURED cause of this report's verdict, and the remedy that fits it.

    One definition, used by the CLI, by `settlements/plan.py`'s gate and by
    the selftest, because the defect this replaces was three renderings of one
    verdict where only one of them had seen the measurement.
    """
    if rep["ok"]:
        return f"emits {rep['commands']} command(s)"
    if not rep.get("resolved", True):
        return (f"the blueprint library does not resolve this name "
                f"({rep.get('error', 'no body resolves for this name')}). "
                f"That is a LIBRARY question, not a geometry one: no "
                f"emitter flag can repair it -- materialise the library "
                f"first")
    if rep["missing"]:
        names = ", ".join(m["prefab"] for m in rep["missing"][:6])
        more = (f", +{len(rep['missing']) - 6} more"
                if len(rep["missing"]) > 6 else "")
        return (f"{len(rep['missing'])} prefab(s) MEASURED absent from the "
                f"full prefab evidence: {names}{more}. --drop-prefab would "
                f"retire them, which costs the body those pieces")
    if rep["refusals"]:
        parts = []
        for r in rep["refusals"]:
            head = r["detail"].split(". ")[0].strip()
            parts.append(f"{r['code']}: {head} (override {r['override']})")
        applied = ("the repairs " + ", ".join(rep["repairs"]) + " did not clear"
                   if rep["repairs"] else "no repair was applied to")
        return (f"{len(rep['refusals'])} geometry preflight refusal(s) that "
                f"{applied}; " + "; ".join(parts))
    return (f"the emitter refused and named no code; its last words were: "
            f"{rep.get('stderr_tail', '')[-240:].strip()}")


def check(name: str, at=(0.0, 0.0, 0.0), yaw: float = 0.0,
          repairs=REPAIRS) -> dict:
    """Run the real plan emitter and report what it says, with its reason.

    A body passes only if a plan comes out. `missing` lists prefabs the
    evidence MEASURED absent; `refusals` lists the geometry preflight's own
    verdicts, each with the flag the emitter named for it. `repaired` records
    what the deterministic repairs actually moved, so a body that passes says
    HOW, and a body that refuses says what survived a repair.
    """
    libmat = _resolver()
    body = libmat.locate_by_filename(name)
    blank = {"name": name, "missing": [], "refusals": [], "repaired": [],
             "repairs": list(repairs), "commands": 0, "stderr_tail": ""}
    if body is None:
        rep = dict(blank, ok=False, resolved=False,
                   error="no body resolves for this name")
        rep["reason"] = reason(rep)
        return rep
    out = Path("/tmp/settle/spawnable") / f"{Path(name).stem}.plan"
    out.parent.mkdir(parents=True, exist_ok=True)
    # EVERY repair is stated, on or off, so the run is the configuration
    # `repairs` names whatever the emitter's own defaults are.
    off = [f"--no-{r[2:]}" for r in REPAIRS if r not in repairs]
    proc = subprocess.run(
        [sys.executable, str(JUMPSTART / "blueprints/to_rcon_plan.py"), str(body),
         "--at", f"{at[0]}", f"{at[1]}", f"{at[2]}", "--rotate", f"{yaw}",
         "--align", "floor-center", "--out", str(out), *repairs, *off],
        capture_output=True, text=True)
    text = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
    missing = []
    if "cannot be spawned" in text:
        for prefab, why in MISSING_RE.findall(text.split("cannot be spawned")[1]):
            missing.append({"prefab": prefab, "why": why.strip()})
    refusals = [{"override": o, "code": c, "detail": d.strip()}
                for o, c, d in REFUSED_RE.findall(text)]
    lines = len([l for l in out.read_text().splitlines() if l.strip()]) if ok else 0
    rep = {"name": name, "ok": ok and not missing, "resolved": True,
           "missing": missing, "refusals": refusals,
           "repaired": [r.strip() for r in REPAIRED_RE.findall(text)],
           "repairs": list(repairs), "commands": lines,
           # Long enough to carry a whole refusal. The 400-character tail
           # this replaces truncated every refusal's numbers away and kept
           # only the summary sentence, which is why the summary was the
           # only thing anyone could quote.
           "stderr_tail": text[-4000:] if not ok else ""}
    rep["reason"] = reason(rep)
    return rep


# ---------------------------------------------------------------------------
# THE CASE. It CONSTRUCTS the states it tests -- no world, no server, no
# library, no corpus -- because a fixture that borrows one inherits that
# artefact's mutability, and this one has to keep working on a world that
# does not exist yet.
# ---------------------------------------------------------------------------
def _old_message(rep: dict) -> str:
    """The message this replaces, RESTORED VERBATIM from `plan.py`.

    It built its rejection list from `rep["missing"]` alone and then named
    `--drop-prefab`. Kept here so the case can show it going red on the state
    it was shipped against, rather than asserting that it used to be wrong.
    """
    unspawnable = [(rep["name"], [m["prefab"] for m in rep["missing"]])]
    return (f"these bodies cannot be spawned without --drop-prefab and are "
            f"not acceptable choices: {unspawnable}. Replace them in the spec.")


def selftest() -> int:
    """Three states and what each message must say about them."""
    # STATE 1 -- the one the whole gate was wrong about: a geometry preflight
    # refusal. `missing` is empty because no prefab is missing.
    preflight_only = {
        "name": "constructed_body.blueprint", "ok": False, "resolved": True,
        "missing": [], "repairs": list(REPAIRS), "commands": 0,
        "repaired": ["4 panel(s) yawed 180 degrees onto their own ridge"],
        "refusals": [{"code": "unsheltered_deck",
                      "override": "--allow-unsheltered-deck",
                      "detail": "10 deck column(s) of this blueprint are "
                                "ENCLOSED BY WALLS and OPEN TO THE SKY. A "
                                "walled sample with nothing above it is a "
                                "hole in the roof."}],
        "stderr_tail": ""}
    # STATE 2 -- a real missing prefab. --drop-prefab IS the remedy here, and
    # the message must still say so.
    missing_prefab = {
        "name": "constructed_body.blueprint", "ok": False, "resolved": True,
        "missing": [{"prefab": "Placeable_Nonesuch",
                     "why": "MISSING from every bundle and deployed mod"}],
        "refusals": [], "repaired": [], "repairs": list(REPAIRS),
        "commands": 0, "stderr_tail": ""}
    # STATE 3 -- the library never resolved the name. No emitter flag repairs
    # that, and collapsing it into the geometry message would send an
    # operator to three flags for a missing checkout.
    unresolved = {
        "name": "constructed_body.blueprint", "ok": False, "resolved": False,
        "error": "no body resolves for this name", "missing": [],
        "refusals": [], "repaired": [], "repairs": list(REPAIRS),
        "commands": 0, "stderr_tail": ""}

    fails = 0

    def want(label: str, text: str, must: list[str], must_not: list[str]):
        nonlocal fails
        bad = ([f"missing {s!r}" for s in must if s not in text]
               + [f"names {s!r}" for s in must_not if s in text])
        print(f"  {'FAIL' if bad else 'ok  '} {label}"
              + (f" -- {', '.join(bad)}" if bad else ""))
        fails += bool(bad)

    print("case: a preflight refusal names the preflight, not --drop-prefab")
    want("new message", reason(preflight_only),
         ["unsheltered_deck", "--allow-unsheltered-deck", "preflight"],
         ["--drop-prefab"])
    # THE NEUTER: the shipped message, restored verbatim, on the SAME state.
    want("old message (must go red)", _old_message(preflight_only),
         ["--drop-prefab", "[]"], ["unsheltered_deck"])
    print("   ^ the old message printed an EMPTY LIST as the evidence for a "
          "refusal caused by geometry, and named a flag that could not fix it")

    print("case: a genuinely missing prefab still names --drop-prefab")
    want("new message", reason(missing_prefab),
         ["Placeable_Nonesuch", "--drop-prefab"], ["preflight"])

    print("case: an unresolved name is a library fault, not a geometry one")
    want("new message", reason(unresolved),
         ["library", "materialise"],
         ["--drop-prefab", "--upright-panels", "preflight"])

    print(f"\n{fails} case(s) failed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bodies", nargs="*")
    ap.add_argument("--plan-json")
    ap.add_argument("--no-repairs", action="store_true",
                    help="run the emitter with none of its own deterministic "
                         "repairs, which is what this gate used to do")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    names = list(a.bodies)
    if a.plan_json:
        names += sorted(json.loads(Path(a.plan_json).read_text())["bodies"])
    repairs = () if a.no_repairs else REPAIRS
    bad = 0
    for n in dict.fromkeys(names):
        r = check(n, repairs=repairs)
        flag = "OK  " if r["ok"] else "FAIL"
        print(f"{flag} {n[:56]:58s} commands={r['commands']:5d} {r['reason']}")
        for line in r["repaired"]:
            print(f"       repaired {line}")
        bad += 0 if r["ok"] else 1
    print(f"\n{bad} of {len(dict.fromkeys(names))} bodies cannot be spawned "
          f"{'as-is' if a.no_repairs else 'even with ' + ' '.join(REPAIRS)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
