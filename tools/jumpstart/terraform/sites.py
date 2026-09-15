#!/usr/bin/env python3
"""Enumerate Ulfsland's solved placements and turn each into a spawn plan.

Two jobs a driver needs and neither placements.yaml nor to_rcon_plan.py does on
its own: find each blueprint BODY (the corpus is not vendored, so bodies are
copied from source at use time and verified by the manifest SHA-256), and place
the building at the FLATTENED altitude rather than the raw ground drop --
`solved.y` is the HIGHEST terrain cell in the footprint, and once the pad is
levelled the right altitude is `flatten_cost.target_y`.

What lands on that altitude is the blueprint's FLOOR PLANE, measured from real
piece colliders by `blueprints/base_geometry.py` and applied by
`to_rcon_plan.py --align floor-center`. The old `--align ground-center` dropped
the lowest PIVOT there instead, which floated every building by the distance
from its lowest pivot to its floor -- 1.50 m on pre-bonemass/iron-era-workshop,
which is the defect the operator reported as the workshop "floating about 10'
high".

It also refuses the duplicate: `pre-elder/early-dock` and
`deepnorth-sandbox/sandbox-harbour` are the same blueprint at the same solved
coordinates, so placing both would stack 1,225 pieces on top of themselves.

Bodies are resolved by `library/materialise.locate_by_filename`, which is the
ONE resolver in this tree. This module used to walk two hardcoded corpus roots
and take the first hit, and that rule is measurably wrong three ways: the
`hs_*` BiomeBlueprints bodies are not in either root, `old_Storgard` holds
ZERO-BYTE stubs of `s-ren-dockhouse.blueprint` and
`salty-dick-cottage-final.blueprint` while another root holds the real bodies,
and a source file name is not unique (`PuP_Minicastle.blueprint` is two
different castles). `locate_by_filename` refuses empty candidates, refuses an
ambiguous name rather than guessing, and requires the body to hash to what the
manifest claims -- so the SHA-256 check below is no longer this module's job.
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

# `library/` and `blueprints/` both contain a `materialise.py`, so the library's
# directory is named explicitly rather than left to import order.
sys.path.insert(0, str(JUMPSTART / "library"))
import materialise  # noqa: E402

# The three loot-spam exploits in this corpus: 361-488 TreasureChest_* objects each.
# Never placed, whatever a placement says.
BANNED = {"15643-Valheimian.blueprint", "14464-Valheimian.blueprint", "gnome_house.blueprint"}

# Prefabs dropped from a body by name, and the two reasons a name appears here.
#
#   MISSING     the converter's evidence file marks it absent from every bundle
#               and deployed mod, so `spawn` would reject it one by one.
#   UNTAGGABLE  a `portal_wood` captured inside a body arrives carrying its
#               AUTHOR's tag, and MEASURED, Game::FindRandomUnconnectedPortal
#               pairs same-tag portals by uniform random draw -- so a portal
#               with an unknown or blank tag is a non-deterministic one-way
#               trip, which is the defect the operator reported. Every portal
#               on this world is placed deliberately with a known tag by
#               `terraform/stock.py`, so a carried one is always dropped.
#
# Per-placement `drop_prefabs:` in placements.yaml adds to this table; the two
# are unioned, so a body-wide rule and a site-specific one cannot shadow each
# other.
DROP_PREFABS = {
    "BjOrN_blueprint001.blueprint": ["piece_Sundial"],
}


def find_body(name: str) -> Path | None:
    """The body this placement names, hash-verified, or None.

    Delegates to the library's single resolver. Returning None for both "no
    such body" and "hash disagrees" would hide the difference, so a mismatch is
    surfaced by the caller comparing the digest itself.
    """
    return materialise.locate_by_filename(name)


def placements(world: str = "Ulfsland") -> list[dict]:
    """Every solved placement, with the duplicates marked.

    Two placements naming the same body at the same solved coordinates are the
    same physical structure, and building both would stack it on itself. WHICH
    of the two is canonical is declared in the data, by a `duplicate_of:
    <preset>#<id>` key on the one that defers -- not inferred from iteration
    order. The first-seen rule this used to apply resolved by directory sort,
    so `deepnorth-sandbox` beat `pre-elder` for the dock because `d` sorts
    before `p`: a silent dependency on a directory name, and it put the
    canonical dock in the tier-9 sandbox instead of at the tier-2 preset that
    needs one.

    An UNDECLARED collision is still caught, and reported as a collision rather
    than silently resolved, because the honest answer to "which of these two did
    you mean" is to ask.
    """
    out = []
    seen: dict[tuple[str, float, float], str] = {}
    root = JUMPSTART / "worlds" / world
    for path in sorted(root.glob("*/placements.yaml")):
        doc = yaml.safe_load(path.read_text())
        preset = path.parent.name
        for place in doc["placements"]:
            solved = place.get("solved") or {}
            if not solved:
                continue
            place = dict(place)
            place["_preset"] = preset
            place["_qualified"] = f"{preset}#{place['id']}"
            out.append(place)
    for place in out:
        solved = place["solved"]
        key = (place["blueprint"], round(float(solved["x"]), 1), round(float(solved["z"]), 1))
        place["_key"] = key
    declared = {p["_qualified"]: p for p in out}
    for place in out:
        defers_to = place.get("duplicate_of")
        if defers_to:
            if defers_to not in declared:
                raise SystemExit(
                    f"{place['_qualified']} declares duplicate_of {defers_to}, which is not a "
                    f"solved placement in this world")
            if declared[defers_to]["_key"] != place["_key"]:
                raise SystemExit(
                    f"{place['_qualified']} declares duplicate_of {defers_to}, but they are not "
                    f"the same body at the same place: {place['_key']} vs "
                    f"{declared[defers_to]['_key']}")
            place["_duplicate_of"] = defers_to
    for place in out:
        if "_duplicate_of" in place:
            continue
        holder = seen.setdefault(place["_key"], place["_qualified"])
        if holder != place["_qualified"]:
            raise SystemExit(
                f"{place['_qualified']} and {holder} are the same body at the same solved "
                f"position and neither declares `duplicate_of` -- say which one is canonical")
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
    rows = []
    for place in placements(args.world):
        solved = place["solved"]
        name = place["blueprint"]
        row = {"preset": place["_preset"], "id": place["id"], "blueprint": name,
               "pieces": place.get("pieces"), "status": "", "plan": "", "commands": 0}
        if "_duplicate_of" in place:
            row["status"] = ("skipped: same structure as " + place["_duplicate_of"]
                             + " (declared duplicate_of)")
            rows.append(row)
            continue
        if name in BANNED:
            row["status"] = "skipped: loot-spam blueprint"
            rows.append(row)
            continue
        body = find_body(name)
        if body is None:
            row["status"] = "skipped: no body resolves for this name (see library/materialise.py)"
            rows.append(row)
            continue
        # `locate_by_filename` returns the mismatched candidate rather than None
        # when a body exists but disagrees with the manifest, so that "missing"
        # and "wrong" stay distinguishable. Re-check here to report which.
        digest = hashlib.sha256(body.read_bytes()).hexdigest()
        expected = place.get("sha256")
        if expected and digest != expected:
            row["status"] = (f"skipped: sha256 {digest[:12]} != placement {expected[:12]} "
                             f"({body})")
            rows.append(row)
            continue
        cost = solved.get("flatten_cost") or {}
        # The pad is the flattened plane, which is what the blueprint's FLOOR has
        # to land on. `solved.y` is the raw ground drop (the highest cell in the
        # footprint) and was never the right altitude once the pad is levelled.
        y = float(cost["target_y"]) if (args.flattened and cost.get("target_y")) else float(solved["y"])
        yaw = float((place.get("rotation") or {}).get("yaw") or 0.0)
        plan = outdir / f"{place['_preset']}__{place['id']}.txt"
        cmd = [sys.executable, str(JUMPSTART / "blueprints" / "to_rcon_plan.py"), str(body),
               "--at", f"{solved['x']}", f"{y}", f"{solved['z']}",
               "--rotate", f"{yaw}", "--align", "floor-center", "--verify",
               "--out", str(plan)]
        # A multi-storey body captured on a slope has no single correct floor
        # plane. Where a placement DECLARES which plane it means, that
        # declaration wins over the measured one and travels with the placement,
        # in the same shape as the rest of placements.yaml: a value plus a
        # written basis. See tools/jumpstart/blueprints/base_geometry.py.
        override = (place.get("blueprint_datum") or {}).get("override_base_y")
        if override is not None:
            cmd += ["--base-y", f"{float(override)}"]
        # Body-wide drops and this placement's own drops are UNIONED: a rule
        # about a prefab (every carried `portal_wood` is untaggable) and a rule
        # about a site must not shadow each other.
        drops = dict.fromkeys(list(DROP_PREFABS.get(name, []))
                              + list(place.get("drop_prefabs") or []))
        row["dropped_prefabs"] = list(drops)
        for prefab in drops:
            cmd += ["--drop-prefab", prefab]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            row["status"] = "plan refused: " + " / ".join(
                ln.strip() for ln in proc.stderr.strip().splitlines()[:4])
            rows.append(row)
            continue
        row["plan"] = str(plan)
        row["commands"] = len(plan.read_text().splitlines())
        # `at` is the PAD: the altitude the blueprint's floor plane is aligned to.
        # `origin_y` is where the blueprint's own origin ends up, which is the pad
        # minus the floor datum, and is what the emitted `spawn` Y values are
        # relative to. Recording both means the index cannot be misread as saying
        # the origin went on the pad, which is the mistake this whole change fixes.
        row["at"] = [solved["x"], y, solved["z"], yaw]
        datum = place.get("blueprint_datum") or {}
        base_y = datum.get("override_base_y", datum.get("base_y"))
        row["pad_y"] = y
        row["base_y"] = base_y
        row["origin_y"] = None if base_y is None else round(y - float(base_y), 3)
        row["datum_method"] = ("declared override" if "override_base_y" in datum
                               else datum.get("method"))
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
