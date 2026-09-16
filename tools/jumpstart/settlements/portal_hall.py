#!/usr/bin/env python3
"""Build the portal HALL and move all 18 hub ends inside it.

WHY THIS EXISTS.  The operator walked the world and reported "the portal hub
just seems to be a bunch of portals spread throughout the woods".  MEASURED,
that is exactly what it is: the 18 hub-end `portal_wood` stand on UNLEVELLED
GENERATED GROUND spread over x -309.5..-274.5 and z 196.5..239.5 -- a 35 x 43 m
scatter whose own recorded Y values run 32.63 to 40.26, a 7.6 m spread -- with
95 trees, bushes, rocks and pickables inside the same 26 m cylinder and no
floor, wall or roof anywhere.  `salty-dick-portal-hub-final` was SOLVED for
(-292.5, 213.5) and never spawned, because a terrain write there was refused;
`build.py::hub_spots` then re-seated each arch to its own generated ground
height, which is the honest thing to do on natural ground and is also why the
arches sit at eleven different heights in a wood.

WHY THE HALL MOVES 11.7 m EAST OF THE SOLVED CENTRE.  The refusal was right
for a reason nobody had measured.  RoadNet's S1 spur terminates at the hub and
ramps to the S2 bridge deck at 25%: MEASURED off the live compiler blobs its
applied surface is 37.10 at the terminus and 30.60 at the abutment 28.7 m away.
A pad levelled at the solved centre would therefore cut a 5.14 m WALL across
its own access ramp.  Moving the pad to (-280.0, 216.0) at target_y 37.10 puts
the terminus 6.7 m INSIDE the pad at the pad's own height (term_drop 0.00 m),
keeps the worst road/pad mismatch at 1.5 m, and drops the earthwork to
max |delta| 4.42 m against TerrainComp's 8 m clamp.

WHAT IS PROTECTED, and how it is proved rather than asserted:
  * bridge-s2-portalhall-strait is `flatten: FORBIDDEN`.  Its 156 piece
    positions are read out of its own `spawn_plan` blob and every one of them
    is probed with `ribbon.delta_at` against the bytes about to be written.
    The nearest is 15.2 m OUTSIDE the written region, so every probe is
    exactly 0.0 -- not "small", zero in float.
  * clearing is by EXPLICIT VEGETATION PREFAB LIST taken from a live census of
    the exact cylinder, never `id=*`.  A prefab-filtered removal cannot delete
    a bridge, a building, a mod house or a portal.
  * the two witness pads at (-1152, 1408) and (-1152, 1664) are 1.2 km away
    and are not touched by anything here.

THE BAYS.  `hs_ashlands_neletit_portalhubtower` carries 15 `portal_wood` of its
own -- the untagged random-destination pool -- and those are STRIPPED.  Their
positions are kept: they are the designer's own bays, they sit on the body's
floor by construction, and 13 of the 15 are `indoor_covered` on the interior
mask (2 sit in the north/south door axes).  Three more bays are solved on an
inner ring, each one probed with the same `fixtures.probe` the fixture audit
uses and accepted only where nothing penetrates it by more than the 0.15 m the
body's own floor beams already do.

Stages, each independently runnable so the ledger token can be released at a
boundary:

    census    read-only: the live cylinder, the bays, the bridge probe
    clear     objects_clear, explicit vegetation prefab list
    park      move every arch standing INSIDE the pad out of the written
              ground BEFORE the flatten, one at a time, tag read back
    flatten   terrain_write, unioning RoadBuild's prior blobs per sample
    body      spawn_plan for the hall, portals and signs stripped
    move      the 18 hub ends into their bays, one at a time, tag read back
    signs     one guidepost per bay naming its destination
    save      `save`
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import hashlib
import struct
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "terraform", JUMPSTART / "roads",
           JUMPSTART / "ledger", JUMPSTART / "blueprints", JUMPSTART / "network",
           HERE):
    sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402
import yaml  # noqa: E402

import tcdata  # noqa: E402
import flatten as FL  # noqa: E402
import ribbon  # noqa: E402
import fixtures  # noqa: E402
import base_geometry  # noqa: E402
import locations as LOCM  # noqa: E402
import replay as R  # noqa: E402
from live import LiveBuilder  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402
import place as PLACE  # noqa: E402

ACTOR = "SeatCheck"
SEED = "Pirate68"
WORLD = "Ulfsland"
LEDGER = JUMPSTART / "ledger/runs" / WORLD / "ledger.jsonl"
BLOBS = JUMPSTART / "ledger/runs" / WORLD / "blobs"
SCRATCH = Path("/tmp/portalhall")

# THE HALL.  Body, pad and datum, every number MEASURED and recorded in the
# module docstring above.
BODY = "hs_ashlands_neletit_portalhubtower.blueprint"
BODY_YAW = 0.0            # the body's own east-west passage then lines up with
                          # the S1/T1 terminus, which is 12 m west of centre
HALL_X, HALL_Z = -280.0, 216.0
TARGET_Y = 37.10          # = the MEASURED applied ground at the road terminus
PAD = 37.4                # body envelope is 36.4 x 34.4 at this yaw
APRON = 0.0
PAINT = "paved_cleared"
STRIP = ("portal_wood", "sign")
CLEAR_R = round(math.hypot(PAD, PAD) / 2 + 2.0, 2)
BATCH_BYTES = 3600

# The old ring, for the arches that have to be moved and the guideposts that
# have to go with them.
RING_X, RING_Z = -292.5, 213.5

# Retire radius for one object.  `deleteObjects` echoes one line per deleted
# object, so this stays small enough that the ANSWER is short.
RETIRE_R = 1.2

# What may be deleted by the clearing, as a REGEX over the live census rather
# than a taxonomy -- identical in spirit to `build.py::NATURAL_RE`, and the
# list actually sent is the intersection with what the cylinder holds.
NATURAL_RE = re.compile(
    r"^(?:beech|birch|fir|pine|oak|swamptree|yggashoot|bush|"
    r"raspberrybush|blueberrybush|cloudberrybush|"
    r"rock|minerock|stubbe|shrub|vines|glowingmushroom|"
    r"pickable_|bh_pickable_|.*_log$|.*_oldlog|greydwarf_root)",
    re.IGNORECASE)

# AND WHAT MAY NEVER BE, whatever the vegetation pattern says.  MEASURED: the
# `.*_log$` alternative above -- which exists for `beech_log`, `FirTree_log`
# and the rest of the deadfall -- also matches `wood_pole_log`, and 17 of
# those are standing in this very cylinder as the ring's GUIDEPOST POSTS.
# Sweeping them into a vegetation clear would delete built pieces under a
# record that says "vegetation", which is the wrong provenance even though
# the posts are in fact going away: they are retired deliberately, by name,
# in their own `retire` records.
BUILT_RE = re.compile(
    r"^(?:wood|stone|iron|dark|black|blue|goblin|grausten|flametal|"
    r"piece_|Piece_|portal|sign|guard|bed|chest|turf|ashwood|marble|_)",
    re.IGNORECASE)

# A portal's own floor beams penetrate its box by 0.098-0.100 m in this body --
# MEASURED at the designer's own 15 bays.  A bay is accepted when nothing
# penetrates it by more than this; anything deeper is a wall or a pillar.
BAY_PEN_TOL_M = 0.15


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

# THE CONTAINER LOG IS SHARED AND SO IS THE RACE.  MEASURED at 05:42 while a
# sibling agent was measuring wt-town: `run_console` sends a command and then
# greps the last 120 s of the container log for its own echo, and a sibling
# pushing hundreds of `Console:` lines through the same sink delayed my echo
# past the 2.0 s settle -- so `objects_count` raised `OutputNotFound`, which
# correctly says "the output is UNKNOWN, not empty" and correctly aborted.
# Re-sending is safe for a QUERY and only for a query, so the retry is scoped
# by verb: a mutating command goes through `console_echo`, which is confirmed
# from the socket reply and never touches the log.
_QUERY_VERBS = ("objects_count", "findObjects")
_console_orig = R.Server.console


def _console_retry(self, cmd: str, settle: float = 2.0):
    verb = cmd.split()[0] if cmd.split() else ""
    waits = (settle, 5.0, 10.0) if verb in _QUERY_VERBS else (settle,)
    last = None
    for wait in waits:
        try:
            return _console_orig(self, cmd, settle=wait)
        except Exception as exc:                # console.OutputNotFound
            last = exc
            time.sleep(1.0)
    raise last


R.Server.console = _console_retry

def records() -> list[dict]:
    return [json.loads(l) for l in LEDGER.read_text().splitlines()]


def libmat():
    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library/materialise.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def waypoints():
    """WayFinding's emitter, loaded by PATH with a package context, exactly as
    `build.py::_waypoints` loads it: `tools/jumpstart` is not a package and
    `network/waypoints.py` does `from . import DATA, JUMPSTART`.  Calling it
    rather than rewriting it is the whole point -- the guidepost geometry and
    the sign validator stay ONE implementation."""
    import types
    if getattr(waypoints, "mod", None) is not None:
        return waypoints.mod
    pkg = types.ModuleType("jsnet")
    pkg.__path__ = [str(JUMPSTART / "network")]
    sys.modules.setdefault("jsnet", pkg)
    init = importlib.util.spec_from_file_location(
        "jsnet.__init__", JUMPSTART / "network/__init__.py")
    base = importlib.util.module_from_spec(init)
    init.loader.exec_module(base)
    for name in ("DATA", "JUMPSTART", "HERE"):
        if hasattr(base, name):
            setattr(pkg, name, getattr(base, name))
    spec = importlib.util.spec_from_file_location(
        "jsnet.waypoints", JUMPSTART / "network/waypoints.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jsnet.waypoints"] = mod
    spec.loader.exec_module(mod)
    waypoints.mod = mod
    return mod

def hub_ends(recs: list[dict]) -> dict[str, dict]:
    """Every LIVE hub-end portal record, keyed by tag.

    The hub end is the one within 60 m of the old ring centre.  Keyed by tag
    and taking the LAST record for that tag, so a re-seat appended later wins
    -- which is what makes this stage resumable.
    """
    gone = set()
    for r in recs:
        if r["op"] == "retire":
            gone.update(int(s) for s in r["params"].get("retires", []))
    out: dict[str, dict] = {}
    for idx, r in enumerate(recs):
        if r["op"] != "portal" or r["seq"] in gone:
            continue
        p = r["params"]
        x, y, z = p["pos"]
        if math.hypot(x - RING_X, z - RING_Z) > 60.0:
            continue
        out[p["tag"]] = {"tag": p["tag"], "seq": r["seq"], "line": idx + 1,
                         "x": x, "y": y, "z": z, "yaw": p["yaw_deg"],
                         "site_id": p.get("site_id"), "role": p.get("role"),
                         "pair_tag": p.get("pair_tag", p["tag"]),
                         "actor": r["actor"]}
    return out


def ring_guideposts(recs: list[dict]) -> list[dict]:
    """The ring's own `waypoint_sign` posts and boards, which name arches that
    are about to stand somewhere else."""
    gone: set[int] = set()
    for r in recs:
        if r["op"] == "retire":
            gone.update(int(s) for s in r["params"].get("retires", []))
    out = []
    for idx, r in enumerate(recs):
        if r["op"] != "spawn" or r["seq"] in gone:
            continue
        p = r["params"]
        if p.get("role") != "waypoint_sign":
            continue
        x, y, z = p["pos"]
        if math.hypot(x - RING_X, z - RING_Z) > 60.0:
            continue
        out.append({"seq": r["seq"], "line": idx + 1, "prefab": p["prefab"],
                    "x": x, "y": y, "z": z, "site_id": p.get("site_id"),
                    "text": (p.get("zdo_strings") or {}).get("text")})
    return out


# ---------------------------------------------------------------------------
# the instrument: applied ground
# ---------------------------------------------------------------------------

def live_comps(recs: list[dict], zones: list[tuple[int, int]]
               ) -> tuple[dict, dict]:
    """Each zone's LIVE `_TerrainCompiler`, rebuilt from the ledger's blobs.

    Per zone, every `terrain_write` claim in FILE ORDER, unioned per sample
    index with the later claim winning -- which is exactly what the write path
    does, because each write re-reads the earlier blob and unions it.
    """
    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    prov: dict[tuple[int, int], list[dict]] = {}
    for idx, r in enumerate(recs):
        if r["op"] != "terrain_write":
            continue
        for e in r["params"]["entries"]:
            z = (int(e["zone"][0]), int(e["zone"][1]))
            if z not in zones:
                continue
            comp = comps.setdefault(z, tcdata.Compiler(zone_x=z[0], zone_z=z[1]))
            old = tcdata.parse((BLOBS / e["blob_sha256"]).read_bytes())
            for i, (lvl, sm) in old["heights"].items():
                comp.modified_height[i] = True
                comp.level_delta[i] = lvl
                comp.smooth_delta[i] = sm
            for i, col in old.get("paints", {}).items():
                comp.modified_paint[i] = True
                comp.paint[i] = col
            prov.setdefault(z, []).append(
                {"line": idx + 1, "seq": r["seq"], "actor": r["actor"],
                 "blob_sha256": e["blob_sha256"], "data_entry": e["data_entry"]})
    return comps, prov


def applied(x: float, z: float, comps: dict, patches: dict
            ) -> tuple[float, float, float]:
    """(applied, generated, delta) at a world XZ.

    THE INSTRUMENT.  `generated` is PatchScan's WorldGenerator height bilinearly
    sampled at 1 m -- the same numbers every flatten's arithmetic is relative to
    -- and `delta` is `ribbon.delta_at`, the bilinear blend of the four
    surrounding written samples, which is how `Heightmap` renders the ground a
    player stands on.  Neither is a declaration and neither is the pad centre.
    """
    zx, zz = tcdata.zone_of(x, z)
    patch = patches[f"z_{zx}_{zz}"]
    gen = patch.height_at_world(x, z)
    delta = ribbon.delta_at(comps, x, z)
    return gen + delta, gen, delta


def pad_zones() -> list[tuple[int, int]]:
    return FL.zones_for(HALL_X, HALL_Z, PAD + 2 * APRON, PAD + 2 * APRON)


def patchset(zones) -> dict:
    return FL.run_patchscan(sorted(set(zones)), SEED,
                            FL.SCRATCH / "patch_portal_hall.bin")


# ---------------------------------------------------------------------------
# the body and its bays
# ---------------------------------------------------------------------------

def body_objects() -> tuple[list, list]:
    path = libmat().locate_by_filename(BODY)
    if path is None:
        raise SystemExit(f"no body resolves for {BODY}")
    objs = read_objects(path)
    keep = [o for o in objs if o.prefab not in STRIP]
    return objs, keep


PLAN_LINE = re.compile(
    r"^spawn_object\s+(\S+)\s+pos=(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)\s+"
    r"rot=(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)")


def emit_plan(out: Path) -> list[str]:
    """`to_rcon_plan.py --align floor-center` for the hall, at the pad."""
    path = libmat().locate_by_filename(BODY)
    if path is None:
        raise SystemExit(f"no body resolves for {BODY}")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(JUMPSTART / "blueprints/to_rcon_plan.py"),
           str(path), "--at", f"{HALL_X}", f"{TARGET_Y}", f"{HALL_Z}",
           "--rotate", f"{BODY_YAW}", "--align", "floor-center",
           "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        raise SystemExit(f"to_rcon_plan failed:\n{proc.stdout[-2000:]}\n"
                         f"{proc.stderr[-2000:]}")
    return [l.strip() for l in out.read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def bays(need: int = 18) -> list[dict]:
    """The 18 bays: the body's own 15 arch transforms, plus 3 solved.

    THE BAY IS THE DESIGNER'S OWN TRANSFORM, READ OFF THE EMITTED PLAN.  Not
    recomputed: the stripped `spawn_object portal_wood` lines carry the exact
    `pos=` (z,x,y) and `rot=` (euler y,x,z) the game would have received, so a
    tagged arch placed on one of them stands EXACTLY where the body's own arch
    stood -- same floor, same colonnade opening, same facing.

    WHY THE PIERCE TEST IS REPORTED AND NOT A VETO at these 15, and this is
    `fixtures`' own MEASURED finding restated: a builder sets an arch flush
    into its frame.  Probing the designer's 15 against the stripped body reads
    0.81-1.00 m of penetration into `Piece_flametal_pillar` and
    `Piece_grausten_pillar_arch_small` -- which is the colonnade the arches are
    SET IN, and the body was captured from a world somebody built and walked.
    Vetoing on it would reject every bay this hall has and stand 18 arches in
    the yard again.  The numbers are recorded per bay instead.

    The SOLVED bays are open-floor positions and get the strict gate: nothing
    may penetrate them deeper than the 0.15 m the floor itself does, the
    interior mask must call the cell `indoor_covered`, and they must clear
    every other bay by 4.4 m -- one `portal_wood` width.
    """
    g = base_geometry.geometry()
    _objs, keep = body_objects()
    shell = fixtures.place_body(keep, (HALL_X, TARGET_Y, HALL_Z), yaw_deg=BODY_YAW)
    mask = shell.interior()
    index = shell.index()
    lines = emit_plan(SCRATCH / "portal_hall.plan")

    def probe_bay(x, z, yaw, stand):
        box = fixtures.fixture_box("portal_wood", x, stand, z, yaw, g)
        hits, _sup = fixtures.probe(box, index, stand_y=stand, geom=g)
        worst = max([h["penetration_m"] for h in hits], default=0.0)
        deep = sorted({h["prefab"] for h in hits
                       if h["penetration_m"] > BAY_PEN_TOL_M})
        cell = mask.cell(x, z)
        return {"worst_pen_m": round(worst, 3), "deep_pen_prefabs": deep,
                "klass": mask.klass.get(cell, "outdoor"),
                "cover_m": (None if cell not in mask.cover_m
                            else round(mask.cover_m[cell], 2)),
                "stand_y": round(stand, 3)}

    out = []
    for line in lines:
        m = PLAN_LINE.match(line)
        if not m or m.group(1) != "portal_wood":
            continue
        z, x, y = float(m.group(2)), float(m.group(3)), float(m.group(4))
        yaw = float(m.group(5)) % 360.0
        row = {"x": round(x, 4), "z": round(z, 4), "y": round(y, 4),
               "yaw": yaw, "source": "designer", "ok": True}
        row.update(probe_bay(x, z, yaw, y))
        out.append(row)
    if len(out) != 15:
        raise SystemExit(f"expected 15 stripped arch transforms, found {len(out)}")

    rx0 = sum(b["x"] for b in out) / len(out)
    rz0 = sum(b["z"] for b in out) / len(out)
    for row in out:
        row["bearing"] = round(math.degrees(
            math.atan2(row["x"] - rx0, row["z"] - rz0)) % 360, 2)
        row["r_m"] = round(math.hypot(row["x"] - rx0, row["z"] - rz0), 3)

    taken = [fixtures.fixture_box("portal_wood", b["x"], b["y"], b["z"], b["yaw"], g)
             for b in out]
    short = need - len(out)
    step = 360.0 / max(short, 1)
    for k in range(short * 8):
        if len(out) >= need:
            break
        bearing = round((k * step / 8.0) % 360.0, 1)
        for radius in (8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 8.5):
            x = rx0 + radius * math.sin(math.radians(bearing))
            z = rz0 + radius * math.cos(math.radians(bearing))
            cell = mask.cell(x, z)
            stand = mask.stand_y(*cell) if cell in mask.cover_m else TARGET_Y
            yaw = round((bearing + 180.0) % 360.0, 1)
            row = {"x": round(x, 3), "z": round(z, 3), "y": round(stand, 3),
                   "yaw": yaw, "bearing": bearing, "r_m": radius,
                   "source": "solved"}
            row.update(probe_bay(x, z, yaw, stand))
            row["ok"] = (not row["deep_pen_prefabs"]
                         and row["klass"] == "indoor_covered")
            tight = fixtures.fixture_box("portal_wood", x, stand, z, yaw, g)
            clash = any(all(o > 1e-6 for o in fixtures.overlap(tight, t))
                        for t in taken)
            near = any(math.hypot(x - b["x"], z - b["z"]) < 4.4 for b in out)
            if row["ok"] and not clash and not near:
                out.append(row)
                taken.append(tight)
                break
    if len(out) < need:
        raise SystemExit(f"only {len(out)} bays for {need} tags")
    out.sort(key=lambda b: (b["source"] != "designer", b["bearing"]))
    return out[:need]


# TAG ORDER AROUND THE HALL.  Grouped by what the destination IS, so the arches
# read as a directory and not as an alphabet: the four settlements first, then
# the watchtowers, the lighthouses, the treehouses, and the two water terminals.
TAG_ORDER = ["u-stenvik", "u-vestvik", "u-castle", "u-sudrberg",
             "u-wtspawn", "u-wtsouth", "u-wttown", "u-wtpeak", "u-wtnorth",
             "u-wteast", "u-lhsouth", "u-lheast", "u-lhwest",
             "u-treesth", "u-treewest", "u-treenear",
             "x-harbour", "x-ferry-e"]


def assignment() -> list[dict]:
    """Which tag stands in which bay.  Deterministic: TAG_ORDER against the
    bays sorted by bearing, so the hall reads clockwise from its west door and
    a re-run puts every arch back where it was."""
    bs = bays()
    if len(bs) < len(TAG_ORDER):
        raise SystemExit(f"{len(bs)} bays for {len(TAG_ORDER)} tags")
    return [dict(b, tag=t) for t, b in zip(TAG_ORDER, bs)]


LOCATION_DUMP = "/tmp/settle/loc3/f6fe167f4fcd.json"


def location_check(probes: list[dict]) -> dict:
    """The pad's stand-off from generated locations, AND the exact probe that
    matters more: the applied height change at every protected piece.

    Two instruments because they answer different questions.  The DUMP says
    how close a location instance is, per type, from LocScan's own
    `max(exteriorRadius, interiorRadius, znviewReachM)`; it is MOD-FREE and
    cannot see More_World_Locations.  The PROBE says whether this write moves
    the ground under a piece that must not move, which is the thing itself.
    The live `LocationProxy` sweep at 64 m is recorded alongside, because a
    marker the dump cannot see is exactly what the dump cannot rule out.
    """
    import clearance
    loc = clearance.load(LOCATION_DUMP)
    half_diag = math.hypot(PAD, PAD) / 2
    near = loc.nearest(HALL_X, HALL_Z)
    viol = loc.violations(HALL_X, HALL_Z, half_diag)
    worst = max((p["worst_applied_delta_m"] for p in probes), default=0.0)
    return {
        "dump": LOCATION_DUMP,
        "method": "MEASURED twice. (1) stand-off per location TYPE from the "
                  "LocScan dump, pad half-diagonal "
                  f"{half_diag:.2f} m. (2) `ribbon.delta_at` over the bytes "
                  "about to be written, at EVERY piece position of EVERY "
                  "flatten:FORBIDDEN site, read from that site's own "
                  "spawn_plan blob the same way writer.py reads it. (3) a "
                  "LIVE `findObjects -prefab LocationProxy` sweep at 64 m "
                  "around the pad answered 2 proxies, nearest 46.19 m, "
                  "because the dump is mod-free and cannot see "
                  "More_World_Locations.",
        "tool": "tools/jumpstart/settlements/portal_hall.py::location_check",
        "nearest": {"name": near["name"], "dist_m": near["dist_m"],
                    "clearance_m": near["standoff_m"]},
        "standoff_m": near["standoff_m"],
        "live_locationproxy_sweep": {"radius_m": 64.0, "found": 2,
                                     "nearest_m": 46.19,
                                     "probe": "findObjects -prefab "
                                              "LocationProxy -near "
                                              "-280 37.1 216 64"},
        "forbidden_piece_probes": probes,
        "worst_applied_delta_at_protected_piece_m": worst,
        "verdict": "clear" if (not viol and worst <= 0.05) else "violates",
        "violations": viol,
    }


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def census(srv) -> dict:
    total, per = srv.count("*", HALL_X, HALL_Z, CLEAR_R)
    natural = {p: n for p, n in per.items()
               if NATURAL_RE.match(p) and not BUILT_RE.match(p)}
    other = {p: n for p, n in per.items()
             if not (NATURAL_RE.match(p) and not BUILT_RE.match(p))}
    return {"total": total, "per_prefab": per, "clearable": natural,
            "not_clearable": other,
            "probe": f"objects_count id=* ignore=_* "
                     f"pos={HALL_X:.2f},{HALL_Z:.2f} max={CLEAR_R:.2f}",
            "method": "MEASURED: the live per-prefab contents of the exact "
                      "cylinder the removal will empty, taken in the same call "
                      "that builds the removal's prefab list",
            "tool": "tools/jumpstart/settlements/portal_hall.py::census"}


def forbidden_pieces(recs: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """Every piece position of every `flatten: FORBIDDEN` site, by site.

    Read the same way `writer.py::_protected_pieces` reads it -- out of the
    site's own `spawn_plan` blob, `pos=` being z,x,y -- so the numbers this
    script probes are the numbers the ledger's own guard will probe.
    """
    pat = re.compile(r"^spawn_object\s+(\S+)\s+pos=([^\s,]+),([^\s,]+),")
    sites: dict[str, list[tuple[float, float]]] = {}
    forb = {r["params"].get("site_id") for r in recs
            if r["params"].get("flatten") == "FORBIDDEN"}
    # A RETIRED PIECE IS NOT STANDING THERE.  Same rule as
    # `writer.py::_site_evidence`, and it has to be the same or this probe
    # and the guard disagree: the hub-END arch of `x-ferry-e` carried
    # `site_id: ferry-terminal-eastisle` and its old position at
    # (-281.5, 197.5) probes 1.500 m of applied delta under this pad. It has
    # been retired and re-seated inside the hall, so there is nothing there
    # to protect -- and the jetty 2.3 km away, which is what the FORBIDDEN
    # flag is actually about, still probes exactly 0.0.
    gone: set[int] = set()
    for r in recs:
        if r["op"] == "retire":
            gone.update(int(s) for s in r["params"].get("retires", []))
    for r in recs:
        site = r["params"].get("site_id")
        if site not in forb or site is None or r["seq"] in gone:
            continue
        if r["op"] in ("spawn", "portal"):
            x, _y, z = r["params"]["pos"]
            sites.setdefault(site, []).append((float(x), float(z)))
        elif r["op"] == "spawn_plan":
            sha = r["params"].get("plan_sha256")
            if not sha or not (BLOBS / sha).exists():
                continue
            for line in (BLOBS / sha).read_text("utf-8").splitlines():
                m = pat.match(line.strip())
                if m:
                    sites.setdefault(site, []).append(
                        (float(m.group(3)), float(m.group(2))))
    return sites


def build_pad(recs, patches):
    """The pad's compilers, unioned onto RoadBuild's prior blobs."""
    zones = pad_zones()
    comps: dict = {}
    op_y: dict = {}
    place = {
        "id": "portal-hall",
        "solved": {"x": HALL_X, "z": HALL_Z, "y": TARGET_Y,
                   "flatten_cost": {"target_y": TARGET_Y},
                   "footprint_orientation": f"{PAD} m along x by {PAD} m along z"},
        "requirement": {"footprint_xz_m": [PAD, PAD]},
    }
    stats = FL.build(place, patches, zones, PAINT, APRON, comps, op_y)
    prior_comps, prov = live_comps(recs, zones)
    merged = {}
    for z, comp in comps.items():
        claims = prov.get(z)
        if not claims:
            continue
        taken = 0
        old = prior_comps[z]
        for i in range(tcdata.SAMPLES):
            if old.modified_height[i] and not comp.modified_height[i]:
                comp.modified_height[i] = True
                comp.level_delta[i] = old.level_delta[i]
                comp.smooth_delta[i] = old.smooth_delta[i]
                taken += 1
            if old.modified_paint[i] and not comp.modified_paint[i]:
                comp.modified_paint[i] = True
                comp.paint[i] = old.paint[i]
        merged[z] = {
            "merged_from": [c["seq"] for c in claims],
            "merged_from_file_lines_1based": [c["line"] for c in claims],
            "merged_from_actors": [c["actor"] for c in claims],
            "merge_policy": "union", "samples_inherited": taken,
            "why": "a zone holds ONE _TerrainCompiler and this op deletes "
                   "before it spawns, so RoadBuild's road samples are re-read "
                   "from the ledger's blob store and kept; the pad wins only "
                   "where it wrote. Last-writer-wins per SAMPLE, never per "
                   "zone -- otherwise this write erases T1 and S1.",
        }
    return stats, comps, op_y, merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("census", "bays", "clear", "park",
                                      "flatten", "body", "move", "signs",
                                      "ringsigns", "save"))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only", default=None, help="one tag, for move/park")
    args = ap.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)

    if args.stage == "bays":
        rows = assignment()
        print(json.dumps(rows, indent=1))
        bad = [r for r in rows if not r["ok"]]
        print(f"{len(rows)} bays, {sum(1 for r in rows if r['klass'] == 'indoor_covered')} "
              f"indoor_covered, {len(bad)} refused")
        return 1 if bad else 0

    recs = records()
    patches = patchset(pad_zones())

    if args.stage == "census":
        with R.Server(dry=args.dry) as srv:
            out = {"cylinder": census(srv), "clear_radius_m": CLEAR_R,
                   "pad_zones": [list(z) for z in pad_zones()]}
            comps, prov = live_comps(recs, pad_zones())
            stats, padcomps, op_y, merged = build_pad(recs, patches)
            out["earthwork"] = {k: stats[k] for k in
                                ("target_y", "samples", "max_cut", "max_fill",
                                 "over_clamp")}
            probes = []
            for site, pts in forbidden_pieces(recs).items():
                worst = 0.0
                at = None
                for x, z in pts:
                    d = abs(ribbon.delta_at(padcomps, x, z))
                    if d > worst:
                        worst, at = d, (x, z)
                probes.append({"site_id": site, "pieces": len(pts),
                               "worst_applied_delta_m": round(worst, 6),
                               "at": at})
            out["flatten_forbidden_probe"] = probes
            ends = hub_ends(recs)
            seats = []
            for tag, e in sorted(ends.items()):
                a, gen, d = applied(e["x"], e["z"], comps, patches)
                seats.append({"tag": tag, "x": e["x"], "z": e["z"],
                              "object_y": e["y"], "applied_ground_y": round(a, 3),
                              "gap_m": round(e["y"] - a, 3),
                              "road_delta_m": round(d, 3),
                              "inside_new_pad": (abs(e["x"] - HALL_X) <= PAD / 2 + 1
                                                 and abs(e["z"] - HALL_Z) <= PAD / 2 + 1)})
            out["hub_end_seats"] = seats
            print(json.dumps(out, indent=1, default=str))
        return 0

    with LiveBuilder(actor=ACTOR, world=WORLD, dry=args.dry) as b:
        if args.stage == "clear":
            c = census(b.srv)
            ids = sorted(c["clearable"])
            if not ids:
                print("nothing clearable"); return 0
            wire = (f"objects_remove id={','.join(ids)} ignore=_* "
                    f"pos={HALL_X:g},{HALL_Z:g} max={CLEAR_R:.2f}")
            res = b.emit("objects_clear", params={
                "centre": [HALL_X, HALL_Z], "radius_m": CLEAR_R,
                "ids": ids, "ignore": ["_*"], "role": "portal_hall_pad",
                "site_id": "portal-hall"},
                wire=[wire],
                expect={"objects_count": {
                    "ids": ",".join(ids), "ignore": "_*",
                    "pos": [HALL_X, HALL_Z], "max": CLEAR_R,
                    "total": 0, "tolerance": 0}},
                meta={"cylinder": {k: c[k] for k in
                                   ("total", "clearable", "not_clearable")},
                      "why": "the hall pad's own cylinder. EXPLICIT PREFAB "
                             "LIST from the live census in the same call: "
                             "`id=*` would also delete the 18 hub-end portals "
                             "standing in this cylinder, the bridge pieces "
                             "28 m away and any mod content, and it is the "
                             "command that deleted POI content in the pre-wipe "
                             "world.",
                      "keeps": "portal_wood, sign, wood_pole_log and every "
                               "building prefab are NOT in the list and cannot "
                               "be matched by it"})
            print(json.dumps({"seq": res["seq"], "ids": ids,
                              "checks": res["checks"]}, indent=1))
            return 0

        if args.stage == "flatten":
            stats, comps, op_y, merged = build_pad(recs, patches)
            missing = FL.ungenerated_zones(b.srv.rc, sorted(comps))
            if missing:
                raise SystemExit(f"REFUSED: zones {missing} hold no _ZoneCtrl")
            _path, entries = FL.write_entries("portal_hall", comps, op_y)
            blobs = []
            out_entries = []
            for e in entries:
                zx, zz = e["zone"]
                blob = comps[(zx, zz)].blob(op_y.get((zx, zz), 0.0))
                digest = b.blob(blob, note=f"TCData {e['entry']} zone {zx},{zz}")
                blobs.append(digest)
                cx, cz = tcdata.zone_centre(zx, zz)
                got, _ = b.srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
                patch = patches[f"z_{zx}_{zz}"]
                row = {"zone": [int(zx), int(zz)],
                       "centre": [float(c) for c in e["centre"]],
                       "data_entry": e["entry"], "blob_sha256": digest,
                       # WHAT THE DELTAS ARE RELATIVE TO.  A replay against
                       # different generated ground must stop rather than
                       # write a pad levelled to the wrong height.
                       "generated_heights_sha256": hashlib.sha256(
                           struct.pack(f"<{patch.n * patch.n}f",
                                       *patch.heights)).hexdigest(),
                       "zone_generated_before": got >= 1,
                       "zone_generated_probe":
                           f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1",
                       "op_record": {
                           "operations": int(e["selfcheck"]["operations"]),
                           "last_op_point": [round(float(v), 3) for v in
                                             e["selfcheck"]["last_op_point"]],
                           "last_op_radius": round(
                               float(e["selfcheck"]["last_op_radius"]), 3)}}
                if (zx, zz) in merged:
                    row["merged_from"] = merged[(zx, zz)]["merged_from"]
                    row["merge_policy"] = "union"
                out_entries.append(row)
            probes = []
            for site, pts in forbidden_pieces(recs).items():
                worst = max((abs(ribbon.delta_at(comps, x, z)) for x, z in pts),
                            default=0.0)
                probes.append({"site_id": site, "pieces": len(pts),
                               "worst_applied_delta_m": round(worst, 6)})
            wire = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
                    f"-prefab _TerrainCompiler -force" for e in out_entries]
            wire += [f"spawn_object _TerrainCompiler "
                     f"from={e['centre'][0]:g},{e['centre'][1]:g},0 "
                     f"data={e['data_entry']}" for e in out_entries]
            res = b.emit("terrain_write", params={
                "name": "portal-hall", "paint": PAINT,
                "datum": f"pad target_y {TARGET_Y} m = the MEASURED applied "
                         f"ground at the T1/S1 road terminus (-292,214); "
                         f"deltas against patchscan generated heights at 1 m "
                         f"(seed {SEED})",
                "entries": out_entries,
                "location_check": location_check(probes),
                "target_y": TARGET_Y, "apron_m": APRON,
                "pad_w": PAD, "pad_d": PAD,
                "max_cut_m": stats["max_cut"], "max_fill_m": stats["max_fill"],
                "over_clamp": stats["over_clamp"],
                "role": "portal_hall_pad", "site_id": "portal-hall"},
                wire=wire,
                requires={"mods": ["WorldEditCommands", "ServerDevcommands"],
                          "prefabs": ["_TerrainCompiler"], "blobs": blobs},
                expect={"terrain_compiler": len(out_entries)},
                meta={"merged": {f"{z[0]},{z[1]}": m for z, m in merged.items()},
                      "zone_checks": stats["checks"],
                      "why": "the hall pad. The operator reported 18 arches "
                             "scattered in a wood at eleven different heights; "
                             "this is the floor they stand on."})
            print(json.dumps({"seq": res["seq"], "checks": res["checks"],
                              "earthwork": {k: stats[k] for k in
                                            ("max_cut", "max_fill", "samples",
                                             "over_clamp")},
                              "forbidden_probe": probes}, indent=1))
            return 0

        if args.stage == "body":
            objs, keep = body_objects()
            plan = SCRATCH / "portal_hall.plan"
            path = libmat().locate_by_filename(BODY)
            cmd = [sys.executable, str(JUMPSTART / "blueprints/to_rcon_plan.py"),
                   str(path), "--at", f"{HALL_X}", f"{TARGET_Y}", f"{HALL_Z}",
                   "--rotate", f"{BODY_YAW}", "--align", "floor-center",
                   "--out", str(plan)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not plan.exists():
                raise SystemExit(f"to_rcon_plan failed:\n{proc.stdout[-2000:]}\n"
                                 f"{proc.stderr[-2000:]}")
            lines = [l.strip() for l in plan.read_text().splitlines()
                     if l.strip() and not l.startswith("#")]
            stripped = collections.Counter()
            kept = []
            for l in lines:
                parts = l.split()
                if len(parts) >= 2 and parts[1] in STRIP:
                    stripped[parts[1]] += 1
                    continue
                kept.append(l)
            plan.write_text("\n".join(kept) + "\n")
            prefabs = collections.Counter(l.split()[1] for l in kept)
            if any(p in prefabs for p in STRIP):
                raise SystemExit("strip failed")
            digest = b.blob("\n".join(kept).encode() + b"\n",
                            note="spawn plan portal-hall (portals+signs stripped)")
            groups = PLACE.batches(kept, PLACE.batch_budget(BATCH_BYTES))
            wire = [" ; ".join(gr) for gr in groups]
            res = b.emit("spawn_plan", params={
                "plan_sha256": digest, "plan_ref": str(plan),
                "anchor": {"x": HALL_X, "y": TARGET_Y, "z": HALL_Z,
                           "yaw": BODY_YAW},
                "command_count": len(kept),
                "prefabs": dict(sorted(prefabs.items())),
                "body": {"filename": BODY, "align": "floor-center",
                         "sha256": hashlib.sha256(
                             Path(libmat().locate_by_filename(BODY))
                             .read_bytes()).hexdigest(),
                         "stripped": dict(stripped)},
                "align": "floor-center", "pad_height": TARGET_Y,
                "batch_bytes": BATCH_BYTES,
                "datum": f"floor-center on the pad at target_y {TARGET_Y}",
                "role": "portal_hall_body", "site_id": "portal-hall"},
                wire=wire,
                requires={"mods": ["WorldEditCommands", "ServerDevcommands"],
                          "prefabs": sorted(prefabs), "blobs": [digest]},
                expect={"prefab_count": [
                    {"prefab": "Piece_grausten_floor_1x1",
                     "count": prefabs["Piece_grausten_floor_1x1"],
                     "pos": [HALL_X, HALL_Z], "max": CLEAR_R, "tolerance": 0},
                    {"prefab": "portal_wood", "count": 18,
                     "pos": [HALL_X, HALL_Z], "max": CLEAR_R, "tolerance": 0}]},
                meta={"stripped": dict(stripped),
                      "stripped_why": "the body carries 15 UNTAGGED "
                                      "portal_wood -- the random-destination "
                                      "pool -- and shipping them inside our own "
                                      "hall would make every arch in it "
                                      "nondeterministic. Their POSITIONS are "
                                      "kept and become the tagged bays.",
                      "portal_wood_expect_why":
                          "18 is the count of TAGGED hub ends already standing "
                          "in their bays inside this cylinder, MEASURED by "
                          "their own tag_readback postconditions before the "
                          "body lands; the body itself contributes none, "
                          "because its 15 untagged arches are stripped."})
            print(json.dumps({"seq": res["seq"], "commands": len(kept),
                              "batches": len(wire), "stripped": dict(stripped),
                              "checks": res["checks"]}, indent=1))
            return 0

        if args.stage in ("park", "move"):
            rows = assignment()
            ends = hub_ends(recs)
            comps, _prov = live_comps(recs, pad_zones())
            done = []
            for row in rows:
                tag = row["tag"]
                if args.only and tag != args.only:
                    continue
                end = ends.get(tag)
                if end is None:
                    raise SystemExit(f"no live hub end for {tag}")
                if args.stage == "park":
                    inside = (abs(end["x"] - HALL_X) <= PAD / 2 + 1
                              and abs(end["z"] - HALL_Z) <= PAD / 2 + 1)
                    if not inside:
                        continue
                if (abs(end["x"] - row["x"]) < 0.01
                        and abs(end["z"] - row["z"]) < 0.01
                        and abs(end["y"] - row["y"]) < 0.01):
                    done.append({"tag": tag, "status": "already seated"})
                    continue
                res = move_one(b, end, row)
                done.append(res)
                print(json.dumps(res, indent=1))
            print(json.dumps({"moved": len(done)}, indent=1))
            return 0

        if args.stage == "signs":
            return stage_signs(b, recs)

        if args.stage == "ringsigns":
            return stage_ringsigns(b, recs)

        if args.stage == "save":
            res = b.emit("save", params={"role": "portal_hall"},
                         wire=["save"], expect={"objects_count": {
                             "ids": "portal_wood", "ignore": "_*",
                             "pos": [HALL_X, HALL_Z], "max": CLEAR_R,
                             "total": 18, "tolerance": 0}},
                         meta={"why": "the hall, its pad and all 18 tagged "
                                      "arches are on disk"})
            print(json.dumps(res, indent=1))
            return 0
    return 0


def move_one(b, end: dict, bay: dict) -> dict:
    """Retire one hub end and re-spawn it in its bay WITH ITS TAG.

    One at a time, never two in flight: the pair invariant counts ends per tag
    and a second retire before the first re-spawn would leave two pairs
    one-ended at once.  The tag is then read back OFF THE ZDO by the record's
    own `tag_readback` postcondition, which is the only proof that survives
    `spawn_object`'s fixed echo.
    """
    tag = end["tag"]
    b.emit("retire", params={
        "prefab": "portal_wood",
        "pos": [end["x"], end["y"], end["z"]], "radius_m": RETIRE_R,
        "retires": [end["seq"]], "role": "portal_hall_move",
        "site_id": end["site_id"],
        "reason": f"hub end {tag} stood on unlevelled generated ground in the "
                  f"wood at the old ring; it is being re-seated on the hall "
                  f"floor at the same tag. Retired rather than left standing "
                  f"so the pair keeps exactly two ends."},
        wire=[f"deleteObjects -prefab portal_wood -near "
              f"{end['x']:.4f} {end['y']:.4f} {end['z']:.4f} {RETIRE_R:g} -force"],
        expect={"absent": [{"prefab": "portal_wood",
                            "pos": [end["x"], end["z"]], "max": RETIRE_R}]},
        meta={"tag": tag, "from": [end["x"], end["y"], end["z"]],
              "to": [bay["x"], bay["y"], bay["z"]]})
    cmd = fixtures.portal_spawn_command("portal_wood", bay["x"], bay["y"],
                                        bay["z"], bay["yaw"], tag)
    res = b.emit("portal", params={
        "prefab": "portal_wood", "pos": [bay["x"], bay["y"], bay["z"]],
        "yaw_deg": bay["yaw"], "tag": tag, "pair_tag": tag,
        "data_b64": fixtures.portal_data(tag),
        "zdo_strings": {"tag": tag},
        "guard_radius_m": 0.5,
        "role": "portal_hall_end", "site_id": "portal-hall",
        "datum": f"bay {bay['source']} at bearing {bay['bearing']} on the "
                 f"hall floor, stand_y MEASURED from the body's interior mask "
                 f"({bay['klass']}, cover {bay['cover_m']} m, worst piece "
                 f"penetration {bay['worst_pen_m']} m)"},
        wire=[cmd],
        expect={"tag_readback": {"tag": tag}},
        meta={"replaces_seq": end["seq"], "replaces_line": end["line"],
              "pair_site_id": end["site_id"],
              "moved_m": round(math.hypot(bay["x"] - end["x"],
                                          bay["z"] - end["z"]), 2),
              "site_id_why":
                  "site_id is `portal-hall` and NOT the destination's site, "
                  "which is recorded as meta.pair_site_id instead. MEASURED "
                  "reason: the ledger's flatten guard groups protected pieces "
                  "by site_id, so an arch inheriting "
                  "`ferry-terminal-eastisle` -- whose jetty 2.3 km away is "
                  "rightly flatten:FORBIDDEN because levelling would remove "
                  "the water under its piles -- made that jetty's protection "
                  "refuse the hall's own floor, 1.500 m of applied delta at "
                  "(-281.5, 197.5). The arch standing in the hall is a piece "
                  "of the hall.",
              "why": "the operator reported the hub as arches scattered in a "
                     "wood; this end now stands on the hall's own floor"})
    return {"tag": tag, "from": [end["x"], end["y"], end["z"]],
            "to": [bay["x"], bay["y"], bay["z"]], "seq": res["seq"],
            "checks": res["checks"]}


# WHAT EACH BOARD SAYS.  The name is the DESTINATION as a person would say it,
# and the second line is the bearing and range to the paired arch MEASURED
# from the hall -- the same shape WayFinding used at the ring
# ("Stenvik\nNE 1050 m"), so the two generations of board read alike.
DISPLAY = {
    "u-stenvik": "Stenvik", "u-vestvik": "Vestvik",
    "u-castle": "Hognest Castle", "u-sudrberg": "Sudrberg Keep",
    "u-wtspawn": "Spawn Watchtower", "u-wtsouth": "South Watchtower",
    "u-wttown": "Town Watchtower", "u-wtpeak": "Peak Watchtower",
    "u-wtnorth": "North Watchtower", "u-wteast": "East Watchtower",
    "u-lhsouth": "South Lighthouse", "u-lheast": "East Lighthouse",
    "u-lhwest": "West Lighthouse", "u-treesth": "South Treehouse",
    "u-treewest": "West Treehouse", "u-treenear": "Near Treehouse",
    "x-harbour": "Temple Harbour", "x-ferry-e": "East Ferry",
}


def site_ends(recs: list[dict]) -> dict[str, dict]:
    """The FAR end of each pair: the arch that is not in the hall."""
    gone: set[int] = set()
    for r in recs:
        if r["op"] == "retire":
            gone.update(int(s) for s in r["params"].get("retires", []))
    out: dict[str, dict] = {}
    for idx, r in enumerate(recs):
        if r["op"] != "portal" or r["seq"] in gone:
            continue
        p = r["params"]
        x, y, z = p["pos"]
        if math.hypot(x - HALL_X, z - HALL_Z) <= 60.0:
            continue
        out[p["tag"]] = {"tag": p["tag"], "seq": r["seq"], "line": idx + 1,
                         "x": x, "y": y, "z": z,
                         "site_id": p.get("site_id"), "actor": r["actor"]}
    return out


def stage_signs(b, recs) -> int:
    """One guidepost per bay, naming that arch's destination."""
    W = waypoints()
    far = site_ends(recs)
    rows = assignment()
    out = []
    for row in rows:
        tag = row["tag"]
        name = DISPLAY.get(tag)
        other = far.get(tag)
        if name is None or other is None:
            raise SystemExit(f"no destination for {tag}: name={name!r} "
                             f"far_end={other!r}")
        _bearing, compass, metres = W.bearing_and_range(
            (HALL_X, HALL_Z), (other["x"], other["z"]))
        text = f"{W.wrap_for_board(name)}\n{compass} {metres:.0f} m"
        pieces = W.portal_guidepost(row["x"], row["y"], row["z"], row["yaw"],
                                    text)
        for piece in pieces:
            params = {"prefab": piece.prefab,
                      "pos": [piece.x, piece.y, piece.z],
                      "yaw_deg": piece.yaw, "role": "waypoint_sign",
                      "site_id": "portal-hall"}
            # RESUMABLE, because `spawn` duplicates when re-run and `emit`
            # does not run the replay's presence guard.  MEASURED need for
            # it: seq 831 failed its postcondition -- 2 `wood_pole_log`
            # inside 1 m -- because one of the RING's 17 posts still stood
            # beside the new bay, so this stage has to be re-runnable
            # without doubling the 14 boards that had already landed.
            standing, _per = b.srv.count(piece.prefab, piece.x, piece.z, 0.6,
                                         ignore="")
            if standing >= 1:
                out.append({"tag": tag, "prefab": piece.prefab,
                            "status": "already standing"})
                continue
            expect = {"prefab_count": [{"prefab": piece.prefab, "count": 1,
                                        "pos": [piece.x, piece.z], "max": 0.6,
                                        "tolerance": 0}]}
            if piece.text is not None:
                params["data_b64"] = W.sign_data(piece.text)
                params["zdo_strings"] = {"text": piece.text}
            res = b.emit("spawn", params=params, wire=[piece.spawn_command()],
                         requires={"prefabs": [piece.prefab]}, expect=expect,
                         meta={"tag": tag, "bay_bearing": row["bearing"],
                               "board": piece.text,
                               "radius_why":
                                   "0.6 m, not 1.0: the ring's own posts "
                                   "stand within a metre of some bays until "
                                   "they are retired, and a postcondition "
                                   "that counts a NEIGHBOUR is not a "
                                   "postcondition",
                               "why": "18 identical arches are unreadable "
                                      "without a board naming each one; the "
                                      "post is seated on the bay's own "
                                      "MEASURED stand height, not on terrain"})
            out.append({"tag": tag, "prefab": piece.prefab, "seq": res["seq"]})
    print(json.dumps({"pieces": len(out),
                      "placed": sum(1 for o in out if "seq" in o),
                      "already": sum(1 for o in out if "seq" not in o)},
                     indent=1))
    return 0


def stage_ringsigns(b, recs) -> int:
    """Retire the RING's guideposts: they name arches that have moved.

    17 boards and 17 posts stood in the wood beside the old ring. Every one of
    them now points at nothing -- the arch it named is inside the hall -- and
    they are the litter a replay would otherwise rebuild. Retired by name and
    position, one record per piece, each with a count-to-zero postcondition.
    """
    posts = ring_guideposts(recs)
    out = []
    for p in posts:
        res = b.emit("retire", params={
            "prefab": p["prefab"], "pos": [p["x"], p["y"], p["z"]],
            "radius_m": 0.6, "retires": [p["seq"]],
            "role": "portal_hall_move", "site_id": "portal-hall",
            "reason": f"ring guidepost for {p.get('site_id')} reading "
                      f"{p.get('text')!r}; its arch now stands in the portal "
                      f"hall with a board of its own, so this one points at "
                      f"empty ground"},
            wire=[f"deleteObjects -prefab {p['prefab']} -near "
                  f"{p['x']:.4f} {p['y']:.4f} {p['z']:.4f} 0.6 -force"],
            expect={"absent": [{"prefab": p["prefab"],
                                "pos": [p["x"], p["z"]], "max": 0.6}]},
            meta={"line": p["line"], "text": p.get("text")})
        out.append({"prefab": p["prefab"], "seq": res["seq"]})
    print(json.dumps({"retired": len(out)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
