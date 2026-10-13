"""REFUSED FOOTPRINTS: WHAT THE LIVE BUILD FOUND THAT THE OFFLINE SOLVE COULD NOT SEE.

The site solver works from the mod-free generator dump.  A body's clearing
cylinder is then swept LIVE after `zones_generate`, and objects the dump never
knew (MEASURED on Pirate68: a `StatueEvil` 73 m from the nearest dumped
location, under lh-south's pad) stop the build with the one refusal that must
never be waived -- `objects_remove` would delete them.  Without a way back, the
refusal is final and the site stays lost; with a hand edit it is a human
repair.  This module is the way back, and the only one: the build RECORDS the
cylinder it refused, and the solver reads the record as an exclusion disc the
next time it places a site, so a fresh run on the same seed/world solves the
site somewhere else by its own rules.

The record is a measurement about ONE world (seed + world are stamped and
checked on read, never guessed).  It is replaced per site, never accumulated
across a different world.  DEPENDS_ON: "the_live_cylinder_sweep".
NEUTER `_MODE = "ignore"`: the solver reads nothing, the pre-record state.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

_MODE = "read"          # read | ignore


def path_for(seed: str, world: str, base: Path) -> Path:
    # OUTSIDE `ledger/runs/<world>/`: a clean regeneration archives that whole
    # directory by rename, and this is a measurement about the generated
    # world (a function of the seed), which a rebuilt ledger does not retire.
    return Path(base) / "ledger" / f"refused-footprints.{world}.json"


def record(path: Path, *, seed: str, world: str, site: str, x: float,
           z: float, radius_m: float, not_clearable: dict) -> dict:
    doc = {"seed": seed, "world": world, "footprints": []}
    if path.exists():
        old = json.loads(path.read_text())
        if (old.get("seed"), old.get("world")) == (seed, world):
            doc = old
    doc["footprints"] = [f for f in doc["footprints"] if f["site"] != site]
    doc["footprints"].append({
        "site": site, "x": round(float(x), 2), "z": round(float(z), 2),
        "radius_m": round(float(radius_m), 2),
        "not_clearable": dict(not_clearable),
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def discs(path: Path, *, seed: str, world: str) -> list[dict]:
    """Exclusion discs for the solver, as `existing_installations` rows.  A
    record of another seed/world is REFUSED, not ignored: an unscoped record
    would silently keep a different world's sites off this world's ground."""
    if _MODE == "ignore" or not Path(path).exists():
        return []
    doc = json.loads(Path(path).read_text())
    if (doc.get("seed"), doc.get("world")) != (seed, world):
        raise SystemExit(
            f"REFUSING: {path} records refusals for seed {doc.get('seed')!r} "
            f"/ world {doc.get('world')!r}, and this solve is seed {seed!r} / "
            f"world {world!r}. Delete it or re-run the build that wrote it.")
    return [{"id": f"refused/{f['site']}", "x": float(f["x"]),
             "z": float(f["z"]), "footprint_m": 2.0 * float(f["radius_m"]),
             "radius_m": float(f["radius_m"]), "refused": f["not_clearable"]}
            for f in doc.get("footprints", [])]


def conflict(x: float, z: float, half_diag_m: float,
             rows: list[dict]) -> dict | None:
    for r in rows:
        need = r["radius_m"] + half_diag_m
        d = math.hypot(x - r["x"], z - r["z"])
        if d < need:
            return dict(r, dist_m=round(d, 1), required_m=round(need, 1))
    return None
