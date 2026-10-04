#!/usr/bin/env python3
"""The LIVE writer for the terminus camp: take the token, place, save, verify.

Kept separate from `terminus_camp.py` so the solve and the checks can be run
and re-run against the live world without a write path in scope at all.
`terminus_camp.py plan` imports nothing from this file.

WHAT THIS FILE IS CAREFUL ABOUT, each with the defect it prevents:

  * THE CHAIN IS RE-HASHED IMMEDIATELY BEFORE RECORD 1 and the write aborts on
    any change, because the token was announced against a hash and a sibling
    appending between the announcement and the first record would make the
    announcement false.
  * `players` IS RECORDED BEFORE EACH WRITE PHASE.  A `players` reply proves
    the main thread ran a frame; the container log cannot, because the
    heartbeat period is 600 s and a quiet log and a dead server look identical
    for ten minutes.
  * PIECES GO BOTTOM-UP IN Y so `WearNTear` support exists when each piece
    lands.  `wood_floor` and `wood_pole` HAVE WearNTear -- unlike the `fb_`
    kit -- so a tile that arrives before its posts is a tile with nothing
    under it.
  * THE COUNT RADIUS IS A GAP MIDPOINT, never a taste pick.  `objects_count`
    MEASURED 2 of 4 pieces at exactly 1.000 m as inside, so no ring may sit ON
    the boundary; and the radius must exclude the `u-nterm` arch at 6.500 m so
    the postcondition cannot pass on somebody else's object.
  * `sfx_*`/`vfx_*` ARE EXCLUDED from the total.  A placed `fire_pit` and
    `piece_groundtorch` spawn their own one-shot `sfx_FireAddFuel` /
    `vfx_groundtorch_addFuel` children, which are TRANSIENT: an exact count
    taken immediately after placement races them.
  * THE READ-BACK IS BOOTSTRAPPED BOTH DIRECTIONS.  Something placed must be
    PRESENT and something never placed must be ABSENT, in the same instrument,
    or a cylinder that answers the same number for everything reads as a pass.
  * THE SAVE IS VERIFIED AS HAVING LANDED -- `World save (N/N) done` in the
    container's own console log plus a world-file mtime -- because the save
    file is RENAMED every save and a held path goes stale.
"""

from __future__ import annotations

import glob
import json
import math
import os
import subprocess
import time
from pathlib import Path

import terminus_camp as TC

BATCH_BYTES = 3600
PIECES_PER_RECORD = 56          # inside the measured 48-64 window
CONTAINER = "valheim-server-Ulfsland"


def world_dir() -> str:
    """THIS run's world's `worlds_local` directory, asked at CALL time: the
    parent of `accept/instruments.py::world_src_default`, the one statement of
    the install layout.  It was Ulfsland's for every world."""
    from accept import instruments as AI  # noqa: PLC0415
    return str(Path(AI.world_src_default()).parent)


# ---------------------------------------------------------------------------
# the token
# ---------------------------------------------------------------------------

def chain_state(actor: str) -> dict:
    """(path, sha256, records, head seq) re-verified through `Ledger.open`
    itself rather than through a shell hash of the file, so the number that is
    announced is the number the writer will use."""
    import hashlib
    from writer import Ledger
    led = Ledger.open(TC.WORLD, actor=actor)
    path = Path(led.path)
    data = path.read_bytes()
    recs = [json.loads(ln) for ln in data.decode().splitlines() if ln.strip()]
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "records": len(recs), "head_seq": recs[-1]["seq"],
            "head_actor": recs[-1].get("actor"),
            "head_op": recs[-1].get("op")}


# ---------------------------------------------------------------------------
# the wire
# ---------------------------------------------------------------------------

def spawn_line(p: dict) -> str:
    """One `spawn_object`.

    `pos=` IS TRANSPOSED: the command takes `z,x,y`, not `x,y,z`.  Three
    component orders exist in this one command family (`pos` z,x,y; `rot`
    euler y,x,z; `from` x,z,y) and picking the wrong one places the piece
    somewhere else entirely without erroring.
    """
    return (f"spawn_object {p['prefab']} "
            f"pos={p['z']:.4f},{p['x']:.4f},{p['y']:.4f} "
            f"rot={p['yaw_deg']:.3f},0,0")


def batched(ps: list[dict]) -> list[list[dict]]:
    """Bottom-up in Y, then chunked by BOTH a piece budget and a byte budget:
    a batch is joined with `;` into one `consoleCommand`, and the reply is
    compared byte-for-byte, so an over-long batch is a confirmation that
    cannot match."""
    order = sorted(ps, key=lambda p: (p["y"], p["prefab"]))
    out, cur, n = [], [], 0
    for p in order:
        line = spawn_line(p)
        if cur and (len(cur) >= PIECES_PER_RECORD
                    or n + len(line) + 1 > BATCH_BYTES):
            out.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += len(line) + 1
    if cur:
        out.append(cur)
    return out


def count_radius(ps: list[dict]) -> dict:
    """The gap-midpoint radius, with the arch as the outer bound."""
    reach = max(math.hypot(p["x"] - TC.CX, p["z"] - TC.CZ) for p in ps)
    arch = math.hypot(TC.ARCH[0] - TC.CX, TC.ARCH[2] - TC.CZ)
    if arch <= reach:
        raise SystemExit(
            f"REFUSING: the arch at {arch:.3f} m is inside this body's own "
            f"reach {reach:.3f} m, so no cylinder can separate them and the "
            f"postcondition would count the arch as one of my pieces.")
    return {"reach_m": round(reach, 3), "arch_m": round(arch, 3),
            "radius_m": round((reach + arch) / 2, 2)}


# ---------------------------------------------------------------------------
# the sink
# ---------------------------------------------------------------------------

def sink_ok() -> dict:
    """The console sink's HOST-side verdict between batches.

    Host `/proc`, not `docker exec`: MEASURED, with supervisord blocked in
    `unix_wait_for_peer` the exec-based probe reported the sink healthy while
    the host-side read of the same two `/proc` entries showed it drainable.
    """
    import rcon
    try:
        return rcon.host_sink_state(CONTAINER)
    except Exception as exc:                          # noqa: BLE001
        return {"state": "unknown", "error": str(exc)[:200]}


def console_log_tail(pattern: str, lines: int = 400) -> list[str]:
    """Read the container's own console log from INSIDE the container.

    Never `docker logs`: a drain consumes the buffered bytes, so the window a
    wedge recovery covers is missing from `docker logs` forever, while
    `/var/log/supervisor/valheim-server-stdout---supervisor-*.log` still has
    it.
    """
    cmd = ["docker", "exec", CONTAINER, "sh", "-c",
           f"tail -n {lines} /var/log/supervisor/"
           f"valheim-server-stdout---supervisor-*.log"]
    try:
        txt = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=60).stdout
    except Exception:                                 # noqa: BLE001
        return []
    return [ln for ln in txt.splitlines() if pattern in ln]


def world_files() -> list[dict]:
    """The save's own files, globbed by mtime -- the `.db2` is RENAMED on every
    save (`_main.<N>.db2`), so a held path goes stale by construction."""
    out = []
    for p in sorted(glob.glob(os.path.join(world_dir(), "*")),
                    key=lambda q: -os.path.getmtime(q))[:6]:
        st = os.stat(p)
        out.append({"file": os.path.basename(p), "bytes": st.st_size,
                    "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(st.st_mtime))})
    return out


# ---------------------------------------------------------------------------
# read-back
# ---------------------------------------------------------------------------

def per_piece_probe(srv, ps: list[dict], r: float = 0.6) -> dict:
    """One CUBE probe per piece, AT THAT PIECE'S OWN Y.

    The per-piece probe replaced the box census as the diff instrument for
    good: a y-blind cylinder over a stacked prefab reported `absent` for
    something standing, and an audit parameter that was added and never used
    made a census blind above 72.98 m and produced 64 duplicates.  This body
    stacks up to three `wood_pole` in one column, so one default y would be
    exactly that mistake.
    """
    rows, missing, dup = [], [], []
    for p in ps:
        rep = srv.command(f"findObjects -prefab {p['prefab']} -near "
                          f"{p['x']:.2f} {p['y']:.2f} {p['z']:.2f} {r}")
        n = rep.count("-Prefab:")
        rows.append({"prefab": p["prefab"],
                     "at": [p["x"], p["y"], p["z"]], "found": n})
        if n == 0:
            missing.append(rows[-1])
        elif n > 1:
            dup.append(rows[-1])
    return {"probed": len(rows), "missing": len(missing),
            "duplicates": len(dup), "missing_rows": missing,
            "duplicate_rows": dup, "cube_radius_m": r}


def readback_bootstrap(srv, ps: list[dict]) -> dict:
    """PRESENT and ABSENT in the same instrument.

    A cylinder or a cube that answers the same thing everywhere passes any
    one-directional check.  So: a prefab this build placed must be found at a
    position this build used, and a prefab this build never placed must NOT be
    found in the same box.
    """
    p = ps[0]
    present = srv.command(f"findObjects -prefab {p['prefab']} -near "
                          f"{p['x']:.2f} {p['y']:.2f} {p['z']:.2f} 1.0")
    never = "wood_wall_log_4x0.5"          # measured registered, never placed
    assert never not in {q["prefab"] for q in ps}
    absent = srv.command(f"findObjects -prefab {never} -near "
                         f"{p['x']:.2f} {p['y']:.2f} {p['z']:.2f} 12.0")
    return {"present_prefab": p["prefab"],
            "present_found": present.count("-Prefab:"),
            "absent_prefab": never,
            "absent_found": absent.count("-Prefab:"),
            "both_directions_ok": (present.count("-Prefab:") >= 1
                                   and absent.count("-Prefab:") == 0)}


def arch_still_standing(srv) -> dict:
    """PortalTriage's contract for sharing a seat: the arch is still there, at
    its own height, with its own tag."""
    rep = srv.command("findObjects -prefab portal_wood -near "
                      f"{TC.ARCH[0]:.2f} {TC.ARCH[1]:.2f} {TC.ARCH[2]:.2f} 4 "
                      "-detailed")
    return {"raw": rep.strip()[:400],
            "found": rep.count("-Prefab:"),
            "tag_u_nterm": "u-nterm" in rep,
            "at_33_56": "33.56" in rep}


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def run(stage: str, actor: str, ap, patches, rep: dict, ps: list[dict],
        out: Path) -> int:
    from live import LiveBuilder

    cr = count_radius(ps)
    tally: dict[str, int] = {}
    for p in ps:
        tally[p["prefab"]] = tally.get(p["prefab"], 0) + 1
    # THE FLAMES ARE NOT IN THE STRUCTURAL BATCHES.  Each one carries a ZDO
    # `data=` payload, and a payload belongs to ONE object: re-spawning a
    # flame to attach it would place a SECOND flame, not configure the first.
    # So they are their own `spawn` records -- the op kind that models
    # `data_b64` -- and the structure goes through `spawn_plan`.
    flames = [p for p in ps if p["prefab"] in TC.FIRE]
    body = [p for p in ps if p["prefab"] not in TC.FIRE]
    batches = batched(body)

    before = chain_state(actor)
    print(json.dumps({"chain_before": before, "count_radius": cr,
                      "batches": [len(b) for b in batches],
                      "flames": [p["prefab"] for p in flames]}, indent=1))

    if stage == "verify":
        import replay as R
        with R.Server() as srv:
            srv.probe()
            v = {"per_prefab": {}, "count_radius": cr}
            for prefab, n in sorted(tally.items()):
                got, _ = srv.count(prefab, TC.CX, TC.CZ, cr["radius_m"])
                v["per_prefab"][prefab] = {"want": n, "got": got,
                                           "ok": got == n, "tolerance": 0}
            v["all_exact_at_tolerance_0"] = all(
                r["ok"] for r in v["per_prefab"].values())
            v["readback_bootstrap"] = readback_bootstrap(srv, ps)
            v["per_piece"] = per_piece_probe(srv, ps)
            v["arch"] = arch_still_standing(srv)
            v["grounding"] = rep["grounding"]
            v["post_reach"] = rep["post_reach"]
            v["illumination"] = rep["illumination"]
            v["world_files"] = world_files()
        (out / "terminus_camp_verify.json").write_text(
            json.dumps(v, indent=1, sort_keys=True))
        print(json.dumps({k: val for k, val in v.items()
                          if k not in ("per_piece", "grounding")}, indent=1))
        pp = v["per_piece"]
        print(json.dumps({"per_piece": {k: val for k, val in pp.items()
                                        if not k.endswith("_rows")}},
                         indent=1))
        return 0 if (v["all_exact_at_tolerance_0"]
                     and pp["missing"] == 0 and pp["duplicates"] == 0
                     and v["readback_bootstrap"]["both_directions_ok"]
                     and v["arch"]["tag_u_nterm"]) else 1

    if stage == "reroof":
        # THE REPAIR, AND ONLY THE REPAIR.
        # `cover()` caught the as-built roof: all six `wood_roof` pivots were
        # set 1.0 m from the ridge instead of half the body, so the panels
        # crossed AT the ridge and 48 of 96 deck samples were open to the sky.
        # The three `wood_roof_top` caps go with them because their height was
        # derived from the same wrong ridge arithmetic.
        #
        # SCOPED BY PREFAB NAME, NEVER BY `id=*`.  `deleteObjects -prefab
        # wood_roof` cannot touch `portal_wood`: the arch is not in the name.
        # An `id=*` removal over this cylinder WOULD reach it, which is why
        # the name is the safety property and the radius is not.
        redo = [p for p in ps if p["prefab"] in ("wood_roof", "wood_roof_top")]
        with LiveBuilder(actor=actor, world=TC.WORLD) as b:
            players = b.srv.command("players")
            b.note(
                "REPAIR of my own seq 2884. `cover()` -- a check written AFTER "
                "that record landed, and proven against it -- measured the "
                "roof as covering 48 of 96 deck samples, covered_fraction "
                "0.500, with BOTH eaves open to the sky and a 1 m gap between "
                "wall-top and eave. Cause: each wood_roof pivot was set 1.0 m "
                "from the ridge instead of half the body depth, so all six "
                "panels crossed at the ridge instead of meeting there. Every "
                "other check passed -- grounding, post reach, per-prefab count "
                "at tolerance 0, arch guard, illumination -- because none of "
                "them asks what is overhead. Retiring 6 wood_roof and 3 "
                "wood_roof_top BY PREFAB NAME and re-placing them at the "
                "corrected eave pivots. players: "
                f"{players.strip()[:60]!r}. Still ZERO terrain writes.",
                role="site_body", site_id=TC.SITE_ID)
            for pf in ("wood_roof", "wood_roof_top"):
                n = sum(1 for p in redo if p["prefab"] == pf)
                b.emit("retire", params={
                    "prefab": pf,
                    "pos": [TC.CX, 35.80, TC.CZ],
                    "radius_m": 4.0,
                    "retires": [2884],
                    "reason": ("eave pivots set from the ridge instead of the "
                               "walls; see cover() and the note above"),
                    "role": "site_body", "site_id": TC.SITE_ID,
                }, wire=[f"deleteObjects -prefab {pf} -near "
                         f"{TC.CX:.2f} 35.80 {TC.CZ:.2f} 4 -force"],
                    requires={"mods": ["WorldEditCommands"], "prefabs": [pf]},
                    expect={"absent": [
                        {"prefab": pf,
                         "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                         "max": cr["radius_m"]}]},
                    meta={"removing": n,
                          "why": ("the reply is one line PER DELETED OBJECT, "
                                  "so the radius is kept small enough that "
                                  "the ANSWER is short; and counting to ZERO "
                                  "is the only honest proof a demolition "
                                  "happened, because a reply that looks empty "
                                  "is indistinguishable from one truncated")})
            lines = [spawn_line(p) for p in sorted(redo, key=lambda q: q["y"])]
            rt: dict[str, int] = {}
            for p in redo:
                rt[p["prefab"]] = rt.get(p["prefab"], 0) + 1
            b.emit("spawn_plan", params={
                "plan_sha256": b.blob("\n".join(lines).encode(),
                                      note="terminus camp corrected roof"),
                "anchor": {"x": TC.CX, "y": TC.FLOOR_Y, "z": TC.CZ,
                           "yaw": TC.BODY_YAW},
                "command_count": len(lines), "prefabs": sorted(rt),
                "pad_height": TC.FLOOR_Y, "batch_bytes": BATCH_BYTES,
                "datum": ("eave pivot = wall z -/+ ROOF_LOW_Z, so the low edge "
                          "lands ON the wall top and the high edge at the "
                          "ridge"),
                "role": "site_body", "site_id": TC.SITE_ID,
            }, wire=[";".join(lines)],
                requires={"mods": ["WorldEditCommands", "ServerDevcommands"],
                          "prefabs": sorted(rt)},
                expect={"prefab_count": [
                    {"prefab": pf, "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                     "max": cr["radius_m"], "count": n, "tolerance": 0}
                    for pf, n in sorted(rt.items())]},
                meta={"pieces": len(lines),
                      "cover_after": TC.cover(ps)["covered_fraction"]})
            b.emit("save", params={"role": "site_body",
                                   "site_id": TC.SITE_ID},
                   wire=["save"],
                   expect={"prefab_count": [
                       {"prefab": pf,
                        "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                        "max": cr["radius_m"], "count": n, "tolerance": 0}
                       for pf, n in sorted(tally.items())]},
                   meta={"pieces": len(ps), "after": "reroof"})
            after = chain_state(actor)
        time.sleep(8.0)
        result = {"chain_after": after,
                  "save_lines": console_log_tail("World save")[-4:],
                  "world_files": world_files(), "sink_after": sink_ok(),
                  "cover": TC.cover(ps)}
        (out / "terminus_camp_reroof.json").write_text(
            json.dumps(result, indent=1, sort_keys=True))
        print(json.dumps({k: v for k, v in result.items()
                          if k != "cover"}, indent=1))
        print(json.dumps({"cover": {k: v for k, v in result["cover"].items()
                                    if k != "open_cells"}}, indent=1))
        return 0

    if stage == "save":
        # RESUME AFTER A REFUSED SAVE, AND NOTHING ELSE.
        # The build's 50 pieces landed as seq 2883-2887, each with its own
        # verified postcondition, and only the `save` record was refused (for
        # an empty `expect`). Re-running `build` would re-send all 50
        # `spawn_object` lines and place a SECOND copy of the body, so the
        # resume is its own stage rather than a retry of the previous one: the
        # only legal repair for a half-finished unit is the part that did not
        # happen. This branch places nothing.
        with LiveBuilder(actor=actor, world=TC.WORLD) as b:
            now = chain_state(actor)
            players = b.srv.command("players")
            b.emit("save", params={"role": "site_body",
                                   "site_id": TC.SITE_ID},
                   wire=["save"],
                   expect={"prefab_count": [
                       {"prefab": pf,
                        "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                        "max": cr["radius_m"], "count": n, "tolerance": 0}
                       for pf, n in sorted(tally.items())]},
                   meta={"pieces": len(ps), "resume_of": "seq 2883-2887",
                         "players_before": players.strip()[:80],
                         "chain_at": now["sha256"],
                         "why": ("the build's save was refused for an empty "
                                 "expect after the pieces had landed; this "
                                 "record is that save with a real "
                                 "postcondition and places nothing")})
            after = chain_state(actor)
        time.sleep(8.0)
        result = {"chain_after": after,
                  "save_lines": console_log_tail("World save")[-4:],
                  "world_files": world_files(), "sink_after": sink_ok()}
        (out / "terminus_camp_save.json").write_text(
            json.dumps(result, indent=1, sort_keys=True))
        print(json.dumps(result, indent=1))
        return 0

    # -- build ---------------------------------------------------------------
    import roads.lamps as LAMPS
    payload, keys = LAMPS.everburning_payload()

    with LiveBuilder(actor=actor, world=TC.WORLD) as b:
        # RE-HASH IMMEDIATELY BEFORE RECORD 1 AND ABORT ON CHANGE.
        now = chain_state(actor)
        if now["sha256"] != before["sha256"]:
            raise SystemExit(
                f"ABORTING before record 1: the chain moved under me.\n"
                f"  announced {before['sha256']} ({before['records']} records)\n"
                f"  now       {now['sha256']} ({now['records']} records)\n"
                f"The token announcement is no longer true, so nothing is "
                f"written. Re-announce against the new head and re-run.")
        players = b.srv.command("players")
        b.note(
            "TerminusCamp TAKING the token to build the shelter at the "
            "northern road terminus. Chain re-verified at sha "
            f"{now['sha256']} ({now['records']} records, head seq "
            f"{now['head_seq']}) immediately before this record. players: "
            f"{players.strip()[:80]!r}. ZERO terrain writes and ZERO object "
            "removals in this whole unit: the body stands on 12 post columns, "
            "so the `u-nterm` arch's seat cannot be moved by me and there is "
            "no clearing by which it could be deleted.",
            role="site_body", site_id=TC.SITE_ID)

        # THE SPAWN, in batches, sink-checked between.
        for i, batch in enumerate(batches, 1):
            sink = sink_ok()
            bt: dict[str, int] = {}
            for p in batch:
                bt[p["prefab"]] = bt.get(p["prefab"], 0) + 1
            lines = [spawn_line(p) for p in batch]
            placed_so_far: dict[str, int] = {}
            for bb in batches[:i]:
                for p in bb:
                    placed_so_far[p["prefab"]] = placed_so_far.get(
                        p["prefab"], 0) + 1
            b.emit("spawn_plan", params={
                "plan_sha256": b.blob("\n".join(lines).encode(),
                                      note=f"terminus camp batch {i}"),
                "anchor": {"x": TC.CX, "y": TC.FLOOR_Y, "z": TC.CZ,
                           "yaw": TC.BODY_YAW},
                "command_count": len(lines),
                "prefabs": sorted(bt),
                "pad_height": TC.FLOOR_Y,
                "batch_bytes": BATCH_BYTES,
                "datum": (f"deck walking surface at {TC.FLOOR_Y} m = the "
                          f"u-nterm arch's own applied datum; pieces seated "
                          f"from their OWN measured collider spans"),
                "role": "site_body", "site_id": TC.SITE_ID,
            }, wire=[";".join(lines)],
                requires={"mods": ["WorldEditCommands", "ServerDevcommands"],
                          "prefabs": sorted(bt)},
                expect={"prefab_count": [
                    {"prefab": pf, "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                     "max": cr["radius_m"], "count": n, "tolerance": 0}
                    for pf, n in sorted(placed_so_far.items())]},
                meta={"batch": i, "of": len(batches),
                      "pieces": len(batch),
                      "sink_before": sink.get("state"),
                      "count_radius_m": cr["radius_m"],
                      "why": ("bottom-up in Y so WearNTear support exists "
                              "when each piece lands; the postcondition is "
                              "per-prefab at tolerance 0 over the cumulative "
                              "set, and the radius is the gap midpoint "
                              f"between this body's reach {cr['reach_m']} m "
                              f"and the arch at {cr['arch_m']} m so it can "
                              "never pass on the arch")})

        # THE FLAMES, one `spawn` each, each carrying the everburning payload
        # at placement time.  `spawn` is the op kind that models `data_b64`,
        # and attaching a payload at placement is the only shape that does not
        # mean placing a second object.
        seen: dict[str, int] = {}
        for p in flames:
            seen[p["prefab"]] = seen.get(p["prefab"], 0) + 1
            b.emit("spawn", params={
                "prefab": p["prefab"],
                "pos": [p["x"], p["y"], p["z"]],        # WORLD order here
                "yaw_deg": p["yaw_deg"],
                "rot_sent": [p["yaw_deg"], 0.0, 0.0],
                "data_b64": payload,
                "guard_radius_m": 0.6,
                "datum": (f"seated from its own measured span: "
                          f"{p['prefab']} at y {p['y']}"),
                "role": "site_prop", "site_id": TC.SITE_ID,
            }, wire=[f"spawn_object {p['prefab']} "
                     f"pos={p['z']:.4f},{p['x']:.4f},{p['y']:.4f} "
                     f"rot={p['yaw_deg']:.3f},0,0 data={payload}"],
                requires={"mods": ["WorldEditCommands"],
                          "prefabs": [p["prefab"]]},
                expect={"prefab_count": [
                    {"prefab": p["prefab"],
                     "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                     "max": cr["radius_m"],
                     "count": seen[p["prefab"]], "tolerance": 0}]},
                meta={"everburning_zdo_ints": keys,
                      "light_range_m": TC.FIRE[p["prefab"]]["high"],
                      "distance_to_arrival_m": round(math.dist(
                          (p["x"], p["z"]), (TC.ARCH[0], TC.ARCH[2])), 3),
                      "why": ("three ZDO INTs and each is load-bearing: "
                              "LoadFields returns at once without HasFields, "
                              "skips the component without "
                              "HasFieldsFireplace, and has nothing to assign "
                              "without Fireplace.m_infiniteFuel. Round-tripped "
                              "through data_entry.decode before sending, "
                              "because an unparseable data= is accepted "
                              "SILENTLY and places the piece with default "
                              "fields. NOTE, measured on this build: "
                              "IsBurning() never consults fuel and "
                              "m_canTurnOff is false on every candidate "
                              "prefab, so the payload keeps the FUEL STATE "
                              "true rather than keeping the flame alive")})

        # A SAVE'S POSTCONDITION IS A RE-READ OF THE WHOLE PLACED SET.
        # MEASURED the hard way: this record was first emitted with `expect={}`
        # and `writer.py` refused it -- "`expect` is MANDATORY and non-empty on
        # a mutating op" -- AFTER all 50 pieces had already landed, leaving the
        # world built and unsaved. The refusal is right and the empty dict was
        # the defect: a save whose postcondition is nothing cannot tell a
        # flushed world from a wedged one. So the save re-counts every prefab
        # of the body at tolerance 0, which is also the cheapest full
        # re-assertion that the batch is still all there at save time.
        b.emit("save", params={"role": "site_body", "site_id": TC.SITE_ID},
               wire=["save"],
               expect={"prefab_count": [
                   {"prefab": pf, "pos": [round(TC.CX, 2), round(TC.CZ, 2)],
                    "max": cr["radius_m"], "count": n, "tolerance": 0}
                   for pf, n in sorted(tally.items())]},
               meta={"pieces": len(ps),
                     "why": ("re-reads all 10 prefabs at tolerance 0 inside "
                             "the gap-midpoint radius; an empty expect here "
                             "was refused by the writer and deserved to be")})
        after = chain_state(actor)

    time.sleep(8.0)
    saved = console_log_tail("World save")
    result = {"chain_after": after,
              "save_lines": saved[-4:], "world_files": world_files(),
              "sink_after": sink_ok()}
    (out / "terminus_camp_build.json").write_text(
        json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps(result, indent=1))
    return 0
