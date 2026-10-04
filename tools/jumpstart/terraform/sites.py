#!/usr/bin/env python3
"""Enumerate a world's solved placements, turn each into a spawn plan, and --
with `--apply --actor <you>` -- SPAWN every plan that could be made THROUGH
THE LEDGER (see `spawn_bodies`), refusing by name every one that could not.

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
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent

# `library/` and `blueprints/` both contain a `materialise.py`, so the library's
# directory is named explicitly rather than left to import order.
sys.path.insert(0, str(JUMPSTART / "library"))
# APPENDED, not inserted: `blueprints/` also holds a `materialise.py` and the
# line above is what decides which one answers.  A second insert at the head
# would silently take that decision back.
sys.path.append(str(HERE))
sys.path.append(str(JUMPSTART))
import materialise  # noqa: E402
import flatten  # noqa: E402  THE placements reader lives here
import worldseed  # noqa: E402

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


def placements(world: str | None = None) -> list[dict]:
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
    world = world or worldseed.world()
    root = JUMPSTART / "worlds" / world
    for path in sorted(root.glob("*/placements.yaml")):
        # EACH PRESET FILE IS ITS OWN DECLARATION and each is scope-checked.
        # Nine files, nine `world:`/`seed:` headers; reading one and calling
        # the sweep checked leaves eight unread.  This function is what the
        # whole plan set is generated from.
        doc = flatten.load_placements(world, path.parent.name)
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
        # `flatten.declared_duplicate`: the predicate clear, flatten and
        # stock ask too, so the four stages agree on which site is built.
        defers_to = flatten.declared_duplicate(place)
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


# ---------------------------------------------------------------------------
# A REFUSED PLAN'S REASON IS READ WHERE IT IS, NOT WHERE IT USED TO FIT.
#
# The status of a refused plan was the emitter's FIRST FOUR stderr lines.
# The emitter prints its datum, then every row each repair touched, and only
# THEN its refusals - so the first four lines are whatever the repairs said.
# MEASURED on the Fresh42 drive (`/tmp/regen2/build8.log`, step 5):
#
#   early-dock             the status ended at the third `preflight DROPPED`
#                          line of 205; its only refusal, co_located_rows (6
#                          rows), was never printed anywhere
#   ashlands-forward-base  the status ended at "Worst:"; its SECOND refusal,
#                          co_located_rows (12 rows), was never printed
#
# UNREAD IS NOT CLEAR.  The verdict is every line from the first line that
# states a refusal to the end, whatever precedes it; everything before it is
# kept as notes and printed; the whole stderr goes into index.json.
# ---------------------------------------------------------------------------
REFUSAL_LINE = re.compile(r"preflight REFUSED|refusing to emit|: no objects$")
STATUS_DEPENDS_ON = (
    "every stderr line from the FIRST line that states a refusal "
    "(REFUSAL_LINE) to the LAST line, wherever that first line sits",
    "the emitter's return code, and ALL of stderr when no line states a "
    "refusal - a refusal with no stated reason is shown whole, never cut",
)


def refusal_status(rc: int, stderr: str) -> tuple[str, list[str]]:
    """The status of a refused plan, and the lines that came before it."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    first = next((i for i, ln in enumerate(lines) if REFUSAL_LINE.search(ln)),
                 None)
    verdict = lines if first is None else lines[first:]
    before = [] if first is None else lines[:first]
    if not verdict:
        verdict = [f"the emitter exited rc {rc} and printed nothing"]
    return f"plan refused (rc {rc}): " + " / ".join(verdict), before


# The two refusals the shipped status cut away, and the missing-prefab shape
# whose NAMES follow its refusal line - each with the facts its status must
# carry.  Built here, from the emitter's own line formats, so the case cannot
# go quiet when a blueprint or a world moves.
_CASE_PAD = [f"  preflight DROPPED row {i} stone_pillar at local (21.651,-7.000,"
             f"1.315), own top -6.000 - 5.097 m under the pad plane"
             for i in range(3)]
_CASES = {
    "early-dock": (4, "\n".join(
        ["  datum base_y=-0.903 (lowest_walkable_surface)"] + _CASE_PAD
        + ["  preflight REPAIRED 205 row(s) removed as wholly below the pad "
           "plane",
           "  preflight REFUSED (override --allow-co-located-rows)   "
           "co_located_rows: 6 rows of this blueprint are CO-LOCATED",
           "s-ren-dockhouse.blueprint: refusing to emit a plan; 1 preflight "
           "refusal(s) above (co_located_rows)."]),
        ("co_located_rows: 6 rows", "--allow-co-located-rows")),
    "ashlands": (4, "\n".join(
        ["  datum props_below_floor: a ground-resting piece sits at y=0.447",
         "  datum base_y=+1.000 (lowest_walkable_surface)",
         "  preflight REPAIRED 8 panel(s) yawed 180 degrees onto their own "
         "ridge",
         "  preflight REFUSED (override --allow-inverted-panels)   "
         "inverted_panels: 0 of 64 rise AWAY and 4 stand UPSIDE DOWN",
         "  preflight REFUSED (override --allow-co-located-rows)   "
         "co_located_rows: 12 rows of this blueprint are CO-LOCATED",
         "hs_ashlands_compact_endgame_base1.blueprint: refusing to emit a "
         "plan; 2 preflight refusal(s) above (inverted_panels, "
         "co_located_rows)."]),
        ("inverted_panels: 0 of 64", "co_located_rows: 12 rows")),
    "missing-prefab": (2, "\n".join(
        ["  datum base_y=+0.000 (lowest_walkable_surface)",
         "body.blueprint: refusing to emit a plan; 1 prefab(s) cannot be "
         "spawned -- MEASURED absent from the full evidence, not merely "
         "absent from a prune:",
         "  Placeable_Nonesuch: MISSING from every bundle and deployed mod",
         "  drop them with --drop-prefab, or override with "
         "--allow-missing-prefabs"]),
        ("Placeable_Nonesuch", "--drop-prefab")),
    "silent": (1, "", ("rc 1",)),
}


def _case_unread(rule) -> dict[str, str]:
    """Per case, the facts its status does NOT carry - as a VERDICT."""
    out = {}
    for name, (rc, err, facts) in _CASES.items():
        status = rule(rc, err)[0]
        miss = [f for f in facts if f not in status]
        out[name] = f"UNREAD {miss}" if miss else "ALL READ"
    return out


def _shipped_status(rc: int, stderr: str) -> tuple[str, list[str]]:
    """THE NEUTER that loses the refusal's position: the shipped rule,
    restored verbatim - the first four stderr lines."""
    return "plan refused: " + " / ".join(
        ln.strip() for ln in stderr.strip().splitlines()[:4]), []


def _matching_only_status(rc: int, stderr: str) -> tuple[str, list[str]]:
    """THE NEUTER that keeps only the lines that SAY refusal: it loses the
    lines that belong to a refusal without repeating the word - the prefab
    names under a missing-prefab refusal - and a silent crash entirely."""
    return "plan refused: " + " / ".join(
        ln.strip() for ln in stderr.splitlines()
        if REFUSAL_LINE.search(ln.strip())), []


def selftest() -> int:
    """`STATUS_DEPENDS_ON` and `SIBLINGS_DEPENDS_ON`, each neutered."""
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    every = {k: "ALL READ" for k in _CASES}
    print("case: every refusal is in the status, wherever the emitter put it")
    want("refusal_status", _case_unread(refusal_status), every)
    print("case: NEUTER -- the shipped first-four-lines status")
    want("shipped (must go red on early-dock and ashlands, the two Fresh42 "
         "refusals it hid)", _case_unread(_shipped_status),
         dict(every, **{"early-dock": "UNREAD ['co_located_rows: 6 rows', "
                                      "'--allow-co-located-rows']",
                        "ashlands": "UNREAD ['co_located_rows: 12 rows']",
                        "silent": "UNREAD ['rc 1']"}))
    print("case: NEUTER -- only the lines that say 'refused'")
    want("matching-only (must go red on the prefab names and on a silent "
         "crash)", _case_unread(_matching_only_status),
         dict(every, **{"missing-prefab": "UNREAD ['Placeable_Nonesuch', "
                                          "'--drop-prefab']",
                        "silent": "UNREAD ['rc 1']"}))
    print("case: a one-site call measures its count radius against the "
          "WORLD's siblings")
    want("sibling_units (--only the workshop: the hall 30 m away bounds it, "
         "the hall's declared duplicate does not)", _case_siblings(),
         ["pre-eikthyr/meadows-starter-hall"])
    print("case: NEUTER -- the shipped set, this call's built plans only")
    global _SIBLING_MODE
    prior, _SIBLING_MODE = _SIBLING_MODE, "call"
    try:
        want("call (must go red: a one-site call has no sibling at all, so "
             "the hall's pieces could answer for the workshop's count)",
             _case_siblings(), [])
    finally:
        _SIBLING_MODE = prior
    print(f"\n{fails} case(s) failed")
    return 1 if fails else 0


def _case_siblings() -> list[str]:
    """`--only pre-bonemass/iron-era-workshop` in a constructed world of three
    solved placements: the workshop, a hall 30 m east, and the hall's declared
    duplicate.  The siblings the workshop's count is bounded by, by name."""
    def place(preset: str, pid: str, x: float, **kw) -> dict:
        return dict({"id": pid, "_preset": preset, "blueprint": f"{pid}.bp",
                     "solved": {"x": x, "z": 0.0, "footprint_orientation":
                                "10 m along x by 10 m along z"}}, **kw)
    shop = place("pre-bonemass", "iron-era-workshop", 0.0)
    hall = place("pre-eikthyr", "meadows-starter-hall", 30.0)
    twin = place("deepnorth-sandbox", "hall-twin", 30.0,
                 _duplicate_of="pre-eikthyr#meadows-starter-hall")
    built = [{"id": shop["id"], "x": 0.0, "z": 0.0, "pad_w": 10.0,
              "pad_d": 10.0}]
    by_id = {p["id"]: f"{p['_preset']}/{p['id']}" for p in (shop, hall, twin)}
    return sorted(by_id[s["id"]] for s in sibling_units([shop, hall, twin], built)
                  if s["id"] != shop["id"])



# ---------------------------------------------------------------------------
# THE BODIES, THROUGH THE LEDGER -- step 5's installation half.
#
# This file used to stop at the plan: it wrote one `to_rcon_plan` file per
# placement and an index, and NOTHING EXECUTED THEM.  MEASURED on the previous
# Fresh42 drive (ledger/runs/Fresh42/archive-contaminated-20260924T2145):
# `sites.py --flattened` ran once at rc 0, 4 of 13 plans came back 'ok'
# (meadows-starter-hall 206, deepnorth-landing-portal 133,
# mistlands-blackforge-base 493, plains-farm-base 872 -- 1,704 spawn
# commands), 8 were refused at the datum preflight and 1 skipped as a declared
# duplicate, and the ledger holds ZERO spawn_plan records naming any of them.
# Step 6 then stocked all 13 sites at rc 0 around bodies that never stood.  A
# stage that writes plans nobody executes is a `1E_OUT_OF_ORDER` debt by
# construction, so `--apply` either SPAWNS every 'ok' plan through the ledger
# -- `settlements/build.py::spawn_plan_record`, the same record a settlement
# body carries, sent by `flatten.install_emit` -- or REFUSES BY NAME, with rc 1,
# every placement whose plan it could not make.
# ---------------------------------------------------------------------------
BODY_ROLE = "site_body"


def body_unit(place: dict, row: dict, digest: str) -> dict:
    """The unit `spawn_plan_record` reads, for one installation body."""
    w, d = flatten.pad_extent(place)
    x, y, z, yaw = row["at"]
    return {"id": place["id"], "x": float(x), "z": float(z),
            "target_y": float(y), "yaw": float(yaw),
            "body": place["blueprint"], "sha256": digest,
            "pad_w": float(w), "pad_d": float(d)}


# THE SIBLINGS A BODY'S COUNT RADIUS IS BOUNDED BY ARE THE WORLD'S, NOT THIS
# CALL'S.  `check_radius` keeps the postcondition's cylinder clear of the
# nearest sibling so a neighbour's identical prefabs cannot answer for this
# body.  The shipped set was the plans THIS CALL built -- harmless while step
# 5 ran every installation in one call, and EMPTY once `buildstage.py` builds
# one site per call (`--only`, one unit per installation: see
# buildstage.py::BODY_UNIT_DEPENDS_ON), which would leave every body's count
# unbounded by the site next door.  So the set is every solved placement that
# can stand -- not a declared duplicate (the SAME body at the same place,
# which would bound its canonical by itself) and not a banned body --
# whatever `--only` narrows the call to, and whether or not its own plan was
# made this time: a body a previous run stood is still standing.
SIBLINGS_DEPENDS_ON = (
    "every solved placement of the world that is not a declared duplicate "
    "and not BANNED, independent of --only and of which plans this call made",
)
_SIBLING_MODE = "world"     # world | call (shipped: this call's built plans)


def sibling_units(places: list[dict], built_units: list[dict]) -> list[dict]:
    """What `check_radius` measures a body's neighbours against."""
    if _SIBLING_MODE == "call":
        return list(built_units)
    out = []
    for place in places:
        if "_duplicate_of" in place or place["blueprint"] in BANNED:
            continue
        w, d = flatten.pad_extent(place)
        out.append({"id": place["id"], "x": float(place["solved"]["x"]),
                    "z": float(place["solved"]["z"]),
                    "pad_w": float(w), "pad_d": float(d)})
    return out


def body_record(b, place: dict, row: dict, unit: dict,
                siblings: list[dict]) -> dict:
    """One installation body's `spawn_plan role=site_body`, in the record
    shape a settlement body carries (`settlements/build.py::
    spawn_plan_record`): the plan is a blob, batched, and the postcondition is
    an exact count inside the radius step 1 cleared."""
    if str(JUMPSTART / "clearing") not in sys.path:
        sys.path.append(str(JUMPSTART / "clearing"))
    import area as clear_area                               # noqa: PLC0415
    plan = Path(row["plan"])
    prefabs: dict[str, int] = {}
    for line in plan.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "spawn_object":
            prefabs[parts[1]] = prefabs.get(parts[1], 0) + 1
    # THE RADIUS STEP 1 CLEARED, at step 1's own defaults: the count that
    # proves the body stands must stay inside ground nothing else can answer
    # from.
    clear_r = clear_area.clearing_area(place, 0.0).radius_m
    rec = flatten._settlement_emitter().spawn_plan_record(
        b, unit, row["preset"], BODY_ROLE, plan, {"prefabs": prefabs},
        siblings, clear_r)
    rec["meta"] = dict({k: v for k, v in rec["meta"].items()
                        if k != "settlement"},
                       installation=f"{row['preset']}/{place['id']}")
    return rec


def spawn_bodies(world: str, actor: str, built: list[tuple[dict, dict, dict]],
                 refused: list[dict], siblings: list[dict]) -> int:
    """Every 'ok' plan, spawned through the ledger; every other one named."""
    if not built and not refused:
        # A declared duplicate alone: its canonical's unit builds the body.
        # Nothing to send, so no session is opened for it.
        return 0
    writer = flatten._ledger("writer")
    PostconditionFailed = flatten._ledger("live").PostconditionFailed
    failed: list[str] = []
    with flatten.live_builder()(actor=actor, world=world) as b:
        recs = b.led.records()
        dead = writer.retired_seqs(recs)
        for place, row, unit in built:
            rec = body_record(b, place, row, unit, siblings)
            # THE LEDGER IS WHAT SAYS A BODY STANDS, so it is what makes this
            # idempotent: a `spawn_plan` is `idempotent="guard"` and a second
            # send stacks the building on itself.
            prior = [r for r in recs if r["op"] == "spawn_plan"
                     and (r.get("params") or {}).get("site_id") == unit["id"]
                     and r["seq"] not in dead]
            if prior:
                same = [r["seq"] for r in prior
                        if r["params"].get("plan_sha256")
                        == rec["params"]["plan_sha256"]]
                if same:
                    print(f"  {row['preset']}/{place['id']}: already standing "
                          f"(spawn_plan seq {same}), not re-sent")
                    continue
                print(f"  REFUSED {row['preset']}/{place['id']}: a DIFFERENT "
                      f"plan already stands here (spawn_plan seq "
                      f"{[r['seq'] for r in prior]}); retire it before "
                      f"spawning a second body on the same pad")
                failed.append(f"{row['preset']}/{place['id']}")
                continue
            try:
                res = flatten.install_emit(b, rec)
            except PostconditionFailed as exc:
                print(f"  FAILED {row['preset']}/{place['id']}: {exc}")
                failed.append(f"{row['preset']}/{place['id']}")
                continue
            print(f"  {row['preset']}/{place['id']}: spawn_plan seq "
                  f"{res.get('seq')} {res.get('status')}, "
                  f"{rec['params']['command_count']} commands in "
                  f"{rec['meta']['batches']} batches, counted "
                  f"{rec['meta']['pieces_inside_count_radius']} inside "
                  f"{rec['meta']['count_radius_m']} m")
        b.srv.command("save")
    for r in refused:
        print(f"  REFUSED {r['preset']}/{r['id']}: {r['status']}")
    if refused or failed:
        print(f"step 5 installation bodies: {len(built) - len(failed)} of "
              f"{len(built) + len(refused)} stand; {len(refused)} refused "
              f"before spawning, {len(failed)} at spawn. Nothing downstream "
              f"may treat the refused ones as built.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/terraform/plans")
    worldseed.add_arguments(ap)
    ap.add_argument("--flattened", action="store_true",
                    help="place at flatten_cost.target_y instead of the raw ground drop")
    ap.add_argument("--only", action="append", default=[], metavar="PRESET/ID",
                    help="restrict to these placements (duplicates and the "
                         "count radius's siblings are still resolved against "
                         "the whole world; a name matching nothing refuses)")
    ap.add_argument("--apply", action="store_true",
                    help="SPAWN every 'ok' plan through the ledger and refuse, "
                         "rc 1, every placement whose plan could not be made")
    ap.add_argument("--actor",
                    help="the agent running this; REQUIRED with --apply, every "
                         "body is appended to the world's ledger before it is sent")
    ap.add_argument("--selftest", action="store_true",
                    help="run the refusal-status case and exit")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    _seed, world = worldseed.apply(args)
    if args.apply and not (args.actor and args.flattened):
        ap.error("--apply needs --actor (the ledger records who spawned the body) "
                 "and --flattened (a body placed at solved.y instead of the pad's "
                 "target_y stands off the levelled ground by the difference)")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    places = placements(world)
    keys = [f"{p['_preset']}/{p['id']}" for p in places]
    # A NAME THAT MATCHES NOTHING IS A REFUSAL, NOT AN EMPTY SUCCESS: step 5
    # builds one site per call, and a misspelt `--only` that answered rc 0
    # with zero rows would journal a body that was never attempted as built.
    unknown = [u for u in args.only if u not in keys]
    if unknown:
        print(f"REFUSED: --only {unknown} names no solved placement of world "
              f"{world!r}; it has {keys}", file=sys.stderr)
        return 2
    rows = []
    built: list[tuple[dict, dict, dict]] = []
    for place in places:
        if args.only and f"{place['_preset']}/{place['id']}" not in args.only:
            continue
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
            row["status"], before = refusal_status(proc.returncode, proc.stderr)
            row["notes"] = " / ".join(before)
            row["stderr"] = [ln.strip() for ln in proc.stderr.splitlines()
                             if ln.strip()]
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
        built.append((place, row, body_unit(place, row, digest)))

    total = sum(r["commands"] for r in rows)
    for r in rows:
        print(f"{r['preset']:20s} {r['id']:28s} {str(r['pieces']):>5s} -> "
              f"{r['commands']:>5d}  {r['status']}")
        if r.get("notes"):
            print(f"    {r['notes']}")
    print(f"total spawn commands: {total}")
    index = outdir / "index.json"
    if args.only:
        # ONE SITE PER CALL MUST NOT ERASE ITS SIBLINGS' ROWS: the index keeps
        # every site's last answer, replaced row by row, in world order.
        try:
            prior = json.loads(index.read_text()) if index.exists() else []
        except ValueError:
            prior = []
        by_key = {f"{r.get('preset')}/{r.get('id')}": r for r in prior
                  if isinstance(r, dict)}
        by_key.update({f"{r['preset']}/{r['id']}": r for r in rows})
        index_rows = [by_key.pop(k) for k in keys if k in by_key] \
            + list(by_key.values())
    else:
        index_rows = rows
    index.write_text(json.dumps(index_rows, indent=1))
    if not args.apply:
        return 0
    refused = [r for r in rows if r["status"] != "ok"
               and not r["status"].startswith("skipped: same structure as")]
    return spawn_bodies(world, args.actor, built, refused,
                        sibling_units(places, [u for _p, _r, u in built]))


if __name__ == "__main__":
    sys.exit(main())
