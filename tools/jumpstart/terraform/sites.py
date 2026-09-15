#!/usr/bin/env python3
"""Enumerate Ulfsland's solved placements and turn each into a spawn plan.

Two jobs a driver needs and neither placements.yaml nor to_rcon_plan.py does on
its own: find each blueprint BODY (the corpus is not vendored, so bodies are
copied from source at use time and verified by the manifest SHA-256), and place
the building at the FLATTENED altitude rather than the solved ground drop --
`solved.y` is where the lowest piece lands on raw terrain, and once the pad is
levelled the right altitude is `flatten_cost.target_y`.

It also refuses the duplicate: `pre-elder/early-dock` and
`deepnorth-sandbox/sandbox-harbour` are the same blueprint at the same solved
coordinates, so placing both would stack 2,036 pieces on top of themselves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent

CORPUS_ROOTS = [
    Path("/media/big4/projects/game/valheim/old/old/old/old_Storgard/config_merged/BepInEx/PlanBuild/blueprints"),
    Path("/media/big4/projects/game/valheim/old/bp_old/config/default/bepinex_old/PlanBuild/blueprints"),
]

# The three loot-spam exploits in this corpus: 361-488 TreasureChest_* objects each.
# Never placed, whatever a placement says.
BANNED = {"15643-Valheimian.blueprint", "14464-Valheimian.blueprint", "gnome_house.blueprint"}

# Prefabs the converter's evidence file marks MISSING from every bundle and deployed mod, so
# `spawn` would reject them one by one.  Dropped by name so the rest of the building still
# goes up, and recorded here rather than hidden behind --allow-missing-prefabs.
DROP_PREFABS = {
    "BjOrN_blueprint001.blueprint": ["piece_Sundial"],
}


def manifest_hashes() -> dict[str, str]:
    doc = json.loads((JUMPSTART / "library" / "data" / "library_manifest.json").read_text())
    return {e["name"]: e.get("sha256", "") for e in doc["entries"]}


def find_body(name: str) -> Path | None:
    for root in CORPUS_ROOTS:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def placements(world: str = "Ulfsland") -> list[dict]:
    out = []
    seen: set[tuple[str, float, float]] = set()
    root = JUMPSTART / "worlds" / world
    for path in sorted(root.glob("*/placements.yaml")):
        doc = yaml.safe_load(path.read_text())
        preset = path.parent.name
        for place in doc["placements"]:
            solved = place.get("solved") or {}
            if not solved:
                continue
            key = (place["blueprint"], round(float(solved["x"]), 1), round(float(solved["z"]), 1))
            if key in seen:
                place = dict(place)
                place["_duplicate_of"] = key
            seen.add(key)
            place["_preset"] = preset
            out.append(place)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/terraform/plans")
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--flattened", action="store_true",
                    help="place at flatten_cost.target_y instead of the raw ground drop")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    hashes = manifest_hashes()
    rows = []
    for place in placements(args.world):
        solved = place["solved"]
        name = place["blueprint"]
        row = {"preset": place["_preset"], "id": place["id"], "blueprint": name,
               "pieces": place.get("pieces"), "status": "", "plan": "", "commands": 0}
        if "_duplicate_of" in place:
            row["status"] = "skipped: duplicate blueprint at the same solved position"
            rows.append(row)
            continue
        if name in BANNED:
            row["status"] = "skipped: loot-spam blueprint"
            rows.append(row)
            continue
        body = find_body(name)
        if body is None:
            row["status"] = "skipped: no body found in any corpus root"
            rows.append(row)
            continue
        digest = hashlib.sha256(body.read_bytes()).hexdigest()
        expected = place.get("sha256") or hashes.get(name)
        if expected and digest != expected:
            row["status"] = f"skipped: sha256 {digest[:12]} != manifest {expected[:12]}"
            rows.append(row)
            continue
        cost = solved.get("flatten_cost") or {}
        y = float(cost["target_y"]) if (args.flattened and cost.get("target_y")) else float(solved["y"])
        yaw = float((place.get("rotation") or {}).get("yaw") or 0.0)
        plan = outdir / f"{place['_preset']}__{place['id']}.txt"
        cmd = [sys.executable, str(JUMPSTART / "blueprints" / "to_rcon_plan.py"), str(body),
               "--at", f"{solved['x']}", f"{y}", f"{solved['z']}",
               "--rotate", f"{yaw}", "--align", "ground-center", "--verify",
               "--out", str(plan)]
        for prefab in DROP_PREFABS.get(name, []):
            cmd += ["--drop-prefab", prefab]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            row["status"] = "plan refused: " + " / ".join(
                ln.strip() for ln in proc.stderr.strip().splitlines()[:4])
            rows.append(row)
            continue
        row["plan"] = str(plan)
        row["commands"] = len(plan.read_text().splitlines())
        row["at"] = [solved["x"], y, solved["z"], yaw]
        row["status"] = "ok"
        row["notes"] = " / ".join(ln.strip() for ln in proc.stderr.strip().splitlines())
        rows.append(row)

    total = sum(r["commands"] for r in rows)
    for r in rows:
        print(f"{r['preset']:20s} {r['id']:28s} {str(r['pieces']):>5s} -> "
              f"{r['commands']:>5d}  {r['status']}")
        if r.get("notes"):
            print(f"    {r['notes']}")
    print(f"total spawn commands: {total}")
    (outdir / "index.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
