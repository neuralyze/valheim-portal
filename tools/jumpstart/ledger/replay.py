#!/usr/bin/env python3
"""Rebuild a world from its ledger, verifying as it goes and refusing on drift.

    ./replay.py preflight --root runs/Ulfsland
    ./replay.py plan      --root runs/Ulfsland
    ./replay.py apply     --root runs/Ulfsland [--from SEQ] [--to SEQ]

WHAT THIS REFUSES TO DO.  There is no repair mode and no fixup mode, by
design.  On drift it stops at the first affected operation and reports.  A
replay that silently substitutes -- a body that resolved to a different file, a
prefab a removed mod used to provide, a delta applied to a height that is no
longer what it was -- produces a world that LOOKS built and is not the world in
the log.  Every silent substitution this project has paid for would have been
cheaper as a refusal.

THE PREAMBLE, and why it is a required recorded step rather than a footnote.
MEASURED by GroundTruth: `ZNet::ServerLoadWorld` branches on
`World::IsChunkedSave()`, and a world CREATED this session has `m_chunkedSave`
false -- so creation takes `ZNet::LoadOldWorld`, ValheimRcon's Harmony
finalizer on `ZNet.LoadWorld` never fires, its listener never starts, and TCP
2458 is absent from `/proc/net/tcp` entirely.  Stopping the server writes the
real chunked save (`_main.N.db2` + `.chunks` + `.fwl2`) and the next boot takes
the chunked path and binds.  A replay runs on exactly a freshly created world,
so it hits this every time, and it reads as "the server is wedged" -- a
diagnosis that was got wrong twice before it was measured.  So:

    RULE: after creating a world from scratch, restart the server once before
    expecting RCON.

The proof of readiness is a successful `players` probe -- a reply proves the
main thread ran a frame -- and never a log read: the heartbeat period is 600 s,
so the log cannot distinguish "quiet" from "dead" inside ten minutes.

VERIFICATION INSTRUMENT, and its one hard rule.  Counts come from Upgrade
World's `objects_count`, read out of the container log, because
`consoleCommand`'s own reply is a fixed echo.  The rule is not "keep replies
under 4050 bytes", it is NEVER ASK A QUESTION WHOSE ANSWER IS LONG: an
`objects_count id=*` over a freshly generated zone answered `Total: 6149`
followed by one line per prefab, went straight to the console sink, and wedged
the server's stdout while the Unity main thread carried on.  So every check
here is scoped to named prefabs or to a small radius, and an unscoped count is
refused by this module before it reaches the wire.

IDEMPOTENCE, honestly stated per op, because resume depends on it:
  zones_generate  yes    SpawnZone skips a zone that IsZoneGenerated
  objects_clear   yes    removing nothing is a no-op
  terrain_write   yes    it deletes the zone's compiler before spawning
  save            yes
  spawn / spawn_plan / portal / stock   NO -- re-running DUPLICATES.  These are
  guarded: the driver counts what is already at the anchor and SKIPS when the
  world already holds the op's output.  That guard is a measurement, not an
  assumption, and when it cannot measure it stops rather than risking a
  double-built house.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "terraform"))

try:
    from . import schema
    from .writer import Ledger, fingerprint, mods_digest
    from .schema import LedgerError
except ImportError:  # run as a script
    import schema
    from writer import Ledger, fingerprint, mods_digest
    from schema import LedgerError

TOTAL_RE = re.compile(r"^Total:?\s*(\d+)\s*$", re.M)
COUNT_LINE_RE = re.compile(r"^(\S+):\s*(\d+)\s*$", re.M)
# Which transport each verb needs.  Three of them, and picking the wrong one
# fails QUIETLY, which is why this is a table rather than a condition at the
# callsite.
#   NATIVE  ValheimRcon's own verbs: sent as a bare RCON command.
#   STAGED  Upgrade World operations that run across frames: they need
#           `stop` / <cmd> / `start`, and the answer immediately after `start`
#           is not the answer.
#   (else)  Server Devcommands / World Edit Commands: through `consoleCommand`.
NATIVE_VERBS = {"save", "players", "deleteObjects"}
# `zones_reset` is in here for a MEASURED reason beyond tidiness: sent bare
# with no `start`, it STAGES a world-wide reset (86 zones, observed) and sits
# in the queue waiting for the next `start` ANYBODY sends.  `uw_check` lists
# the queue and `stop` clears it.
STAGED_VERBS = {"zones_generate", "objects_remove", "objects_reset",
                "zones_reset", "terrain_reset"}

# The guard radius for a non-idempotent single spawn.  Smaller than the
# closest spacing any two same-prefab pieces will ever have, because the
# guard answers "is THIS op's output present", and a neighbour answering yes
# on its behalf silently drops a piece.
GUARD_EPS_M = 0.5


def chain_report(path: Path) -> dict:
    """Walk the ledger in FILE ORDER and report its integrity as DATA.

    Local to the replay side on purpose: `writer.Ledger` raises when the
    chain is broken, because appending to a damaged log is a different
    decision from replaying one, and a replay needs the FACTS about the
    damage rather than an exception.  It reads only `schema.digest`, so it
    does not depend on how the writer chooses to expose this.

    FILE POSITION IS THE ORDER.  MEASURED tonight: three concurrent appends
    raced inside the writer's read-head-compute-prev-write window, two
    branches were written carrying the same `prev`, and seqs 43-46 each
    appear TWICE.  So `seq` is an advisory LABEL from the fork onward and the
    only total order the artefact has is the order its lines were written in
    -- which is also the true causal order here.
    """
    records: list[dict] = []
    if path.exists():
        for raw in path.read_text("utf-8").splitlines():
            raw = raw.strip()
            if raw:
                records.append(json.loads(raw))

    documented: dict[int, dict] = {}
    for rec in records:
        if rec.get("op") == "chain_reset":
            p = rec.get("params", {})
            if "broken_at_line" in p:
                documented[int(p["broken_at_line"])] = p

    head = schema.GENESIS_PREV
    forks: list[dict] = []
    seen: dict = {}
    for i, rec in enumerate(records):
        claimed = rec.get("prev")
        if claimed != head:
            reset = documented.get(i)
            # A reset is a confession and it has to be checkable: one whose
            # stated digests do not match what the file actually holds at
            # that offset would turn a detectable fork into a clean lie.
            ok = bool(reset
                      and reset.get("expected_prev") == claimed
                      and reset.get("actual_prev") == head)
            forks.append({"line": i, "seq": rec.get("seq"),
                          "actor": rec.get("actor"), "op": rec.get("op"),
                          "ts": rec.get("ts"), "declared_prev": claimed,
                          "actual_prev": head, "documented": ok,
                          "reset_present": bool(reset)})
        seen.setdefault(rec.get("seq"), []).append(i)
        head = schema.digest(rec)

    return {"records": records, "count": len(records), "head": head,
            "forks": forks,
            "duplicate_seqs": {s: ls for s, ls in seen.items() if len(ls) > 1}}


class BlobStore:
    """Content-addressed payload reads, verified on the way out."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self, sha: str) -> bytes:
        target = self.path / sha
        if not target.exists():
            raise LedgerError(
                f"blob {sha} is referenced by the ledger and missing from "
                f"{self.path}. The replay cannot reproduce that operation and "
                f"will not guess at a substitute.")
        data = target.read_bytes()
        import hashlib
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            raise LedgerError(
                f"blob {sha} digests to {got}: the stored payload has been "
                f"modified. Refusing to replay it.")
        return data



# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------

class Drift(RuntimeError):
    """The world or the environment is not the one the ledger was recorded
    against.  Always fatal; never repaired."""


def compare_manifest(recorded: dict, live: dict) -> list[dict]:
    """Every difference that matters, classified by whether it is fatal.

    FATAL means replaying would build something other than what the log says.
    ADVISORY means the difference is real and recorded but cannot change the
    bytes that land in the world.
    """
    out: list[dict] = []

    def cmp(field: str, level: str, why: str, a=None, b=None):
        a = recorded.get(field) if a is None else a
        b = live.get(field) if b is None else b
        if a != b:
            out.append({"field": field, "level": level, "recorded": a,
                        "live": b, "why": why})

    cmp("world", "fatal", "a replay pointed at a different world would build "
                          "into someone else's save")
    cmp("seed_name", "fatal", "every TCData delta in this ledger is relative "
                              "to this seed's generated heights")
    cmp("seed", "fatal", "same, numerically")
    cmp("world_gen_version", "fatal",
        "WorldGenerator output is versioned; a different generator makes the "
        "same delta land at a different absolute height")
    cmp("world_version", "advisory",
        "the save format version; it does not change generated terrain, but a "
        "large jump means a game update nobody validated the mod set against")
    cmp("game_build_anchor", "fatal",
        "the mod set was validated against this build; hostops' start gate "
        "refuses a mismatch for the same reason")

    rec_mods, live_mods = recorded.get("mods", {}), live.get("mods", {})
    if not rec_mods.get("measured"):
        out.append({"field": "mods", "level": "fatal",
                    "recorded": "NOT MEASURED", "live": live_mods.get("digest"),
                    "why": "the ledger header was written without hashing the "
                           "plugin set, so 'the same mods' cannot be asserted. "
                           "Not measured is not the same as no drift."})
    elif rec_mods.get("digest") != live_mods.get("digest"):
        a = {m["path"]: m.get("sha256") for m in rec_mods.get("dlls", [])}
        b = {m["path"]: m.get("sha256") for m in live_mods.get("dlls", [])}
        out.append({
            "field": "mods", "level": "fatal",
            "recorded": rec_mods.get("digest"), "live": live_mods.get("digest"),
            "removed": sorted(set(a) - set(b)),
            "added": sorted(set(b) - set(a)),
            "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]),
            "why": "ZoneSystem::PlaceLocations runs whatever locations the "
                   "loaded mods registered, so a different mod set generates "
                   "different POIs under the same seed -- and a pad solved "
                   "with a clearance to a location that no longer exists (or "
                   "now does) is a pad that deletes location content"})

    # Modifiers are NEVER in the save: `ZoneSystem::Save` strips every key
    # resolving to a named `GlobalKeys` member before writing, and
    # `SetStartingGlobalKeys` re-derives them each boot from the launch args.
    # So they can only be compared as OBSERVATIONS, and they are fatal because
    # `resources_muchmore` and `portals_casual` change what the built world
    # MEANS even when every coordinate matches: a road justified by hauling
    # ore is a different road in a world where ore teleports.
    rec_mod = (recorded.get("modifiers") or {})
    live_mod = (live.get("modifiers") or {})
    if rec_mod.get("modifiers") is None:
        out.append({"field": "modifiers", "level": "fatal",
                    "recorded": "NOT MEASURED",
                    "live": live_mod.get("modifiers"),
                    "why": "the header recorded no modifier set, so the "
                           "replay cannot assert the world means the same "
                           "thing. An empty global-key list in a save is "
                           "CORRECT and is not evidence of no modifiers."})
    elif sorted(rec_mod["modifiers"]) != sorted(live_mod.get("modifiers") or []):
        out.append({"field": "modifiers", "level": "fatal",
                    "recorded": rec_mod.get("modifiers"),
                    "recorded_source": rec_mod.get("modifiers_source"),
                    "live": live_mod.get("modifiers"),
                    "live_source": live_mod.get("modifiers_source"),
                    "why": "SERVER_ARGS no longer produce the same modifier "
                           "set; portals_casual alone decides whether ore "
                           "travels through a portal (GlobalKeys 35, read by "
                           "Inventory::IsTeleportable)"})

    rec_tool = recorded.get("tool", {})
    live_tool = live.get("tool", {})
    if rec_tool.get("commit") != live_tool.get("commit"):
        out.append({"field": "tool.commit", "level": "advisory",
                    "recorded": rec_tool.get("commit"),
                    "live": live_tool.get("commit"),
                    "why": "replay re-sends recorded wire strings, so the "
                           "current tool version does not change what lands; "
                           "it matters only if you re-derive"})
    if rec_tool.get("dirty"):
        out.append({"field": "tool.dirty", "level": "advisory",
                    "recorded": True, "live": live_tool.get("dirty"),
                    "why": "the ledger was produced by an uncommitted tree, so "
                           "the generator of these operations cannot be "
                           "reconstructed from the commit alone"})
    if rec_tool.get("schema_version") != schema.SCHEMA_VERSION:
        out.append({"field": "schema_version", "level": "fatal",
                    "recorded": rec_tool.get("schema_version"),
                    "live": schema.SCHEMA_VERSION,
                    "why": "the ledger was written against a different schema; "
                           "field meanings may have moved"})
    return out


# --------------------------------------------------------------------------
# Server surface
# --------------------------------------------------------------------------

class Server:
    """RCON + console, with the bounded-question rule enforced here rather
    than remembered at the callsite."""

    def __init__(self, dry: bool = False):
        self.dry = dry
        self.rc = None
        self.sent: list[str] = []

    def __enter__(self) -> "Server":
        if not self.dry:
            from rcon import Rcon  # noqa: PLC0415  -- terraform/rcon.py
            self.rc = Rcon(timeout=30.0)
            self.rc.connect()
        return self

    def __exit__(self, *_exc) -> None:
        if self.rc:
            self.rc.close()

    def probe(self) -> float:
        """Liveness.  `players` is 8 bytes and 32-35 ms, and a reply proves the
        main thread ran a frame.  The log proves nothing inside 600 s."""
        if self.dry:
            return 0.0
        return self.rc.probe()

    def command(self, cmd: str) -> str:
        self.sent.append(cmd)
        if self.dry:
            return "(dry run)"
        return self.rc.command(cmd)

    def console(self, cmd: str, settle: float = 2.0) -> list[str]:
        self.sent.append(cmd)
        if self.dry:
            return []
        from console import run_console  # noqa: PLC0415
        return run_console(self.rc, cmd, settle=settle)

    def count(self, ident: str, x: float, z: float, radius: float,
              ignore: str = "_*") -> tuple[int, dict[str, int]]:
        """Scoped `objects_count`.  `ident='*'` is allowed only inside a small
        radius: an unbounded star over a generated zone is the question whose
        ANSWER is long, and it wedges the console sink."""
        if ident == "*" and radius > 40.0:
            raise LedgerError(
                f"refusing `objects_count id=*` over a {radius:g} m radius: a "
                f"freshly generated zone answers Total: 6149 followed by one "
                f"line per prefab, straight to the console sink, and that is "
                f"what wedged the server's stdout. Scope to named prefabs.")
        # MEASURED by Crossings on the first real emit: an EMPTY `ignore=`
        # is not "no filter", it is an empty id -- the console answers
        # `Error: Entity id  not recognized.` and prints no Total, so the
        # count correctly refuses and the whole op aborts on a command that
        # was malformed rather than on a world that was wrong.  The token has
        # to be absent, not empty.
        parts = [f"objects_count id={ident}"]
        if ignore:
            parts.append(f"ignore={ignore}")
        parts.append(f"pos={x:.2f},{z:.2f} max={radius:.2f}")
        lines = self.console(" ".join(parts))
        text = "\n".join(lines)
        total = TOTAL_RE.search(text)
        if not total:
            if self.dry:
                return -1, {}
            raise LedgerError(
                f"objects_count printed no Total line; console said {lines!r}. "
                f"An empty answer is not a zero -- that is how a removal gets "
                f"aimed at nothing -- so this is a stop, not a 0.")
        return int(total.group(1)), {n: int(c) for n, c in
                                     COUNT_LINE_RE.findall(text)
                                     if n != "Total"}


def restart_and_wait(world: str, timeout: float = 300.0) -> dict:
    """The mandatory preamble on a freshly created world.

    Creation boots take `ZNet::LoadOldWorld`, so ValheimRcon's listener is
    never started.  One restart writes the chunked save and the next boot
    binds.  Readiness is proved by a `players` reply, not by a log line.
    """
    from rcon import CONTAINER, Rcon  # noqa: PLC0415
    t0 = time.time()
    subprocess.run(["docker", "restart", CONTAINER], check=True,
                   capture_output=True, text=True, timeout=180)
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with Rcon(timeout=10.0) as rc:
                rtt = rc.probe()
                return {"restarted": True, "rcon_rtt_s": round(rtt, 3),
                        "waited_s": round(time.time() - t0, 1),
                        "proof": "players reply -- the main thread ran a frame"}
        except Exception as exc:  # noqa: BLE001 -- the port is simply not up yet
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(5.0)
    raise Drift(f"RCON never bound within {timeout:g}s after a restart of "
                f"{CONTAINER}; last error {last}. On a world created this "
                f"session this is EXPECTED before the first restart and a "
                f"real failure after it.")


# --------------------------------------------------------------------------
# Expectation checks
# --------------------------------------------------------------------------

def check_expect(srv: Server, rec: dict) -> list[dict]:
    """Measure the op's declared postcondition.  Returns one result per check;
    `ok: False` anywhere stops the replay."""
    exp = rec.get("expect") or {}
    results: list[dict] = []

    if "zone_ctrl" in exp:
        # `_ZoneCtrl` is `_`-prefixed, so it must be NAMED: the usual
        # `ignore=_*` would subtract exactly the thing being counted, and an
        # empty `ignore=` is an empty id rather than no filter.
        #
        # PER ZONE, at the zone CENTRE, with max=1.  The earlier form asked
        # one wide `radius + 64` cylinder, which for a 16 m op is an 80 m
        # circle that counts _ZoneCtrl from NEIGHBOURING zones -- so it would
        # have passed for the wrong reason the moment the command worked,
        # which is the defect class this whole project keeps paying for: a
        # check that confidently answers a question it is not measuring.
        # MEASURED by GroundTruth: PlaceZoneCtrl puts the _ZoneCtrl exactly
        # at the zone centre and objects_count's filter is an XZ cylinder, so
        # max=1 is exact and the answer is two lines.
        zones = [tuple(z) for z in rec["params"].get("zones", [])]
        missing = []
        for zx, zz in zones:
            cx, cz = zx * 64.0, zz * 64.0
            got, _ = srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
            if got < 1:
                missing.append([zx, zz])
        results.append({"check": "zone_ctrl", "want": len(zones),
                        "got": len(zones) - len(missing),
                        "ungenerated": missing, "ok": not missing,
                        "why": "one _ZoneCtrl per generated zone, probed at "
                               "each zone centre with max=1 -- exact, and a "
                               "real completion signal for a staged "
                               "operation rather than a sleep"})

    if "objects_count" in exp:
        c = exp["objects_count"]
        got, per = srv.count(c.get("ids", "*"), c["pos"][0], c["pos"][1],
                             float(c["max"]), ignore=c.get("ignore", "_*"))
        tol = int(c.get("tolerance", 0))
        results.append({"check": "objects_count", "want": c["total"],
                        "got": got, "tolerance": tol,
                        "ok": abs(got - int(c["total"])) <= tol,
                        "per_prefab": per})

    if "prefab_count" in exp:
        for c in exp["prefab_count"]:
            got, _ = srv.count(c["prefab"], c["pos"][0], c["pos"][1],
                               float(c["max"]))
            tol = int(c.get("tolerance", 0))
            results.append({"check": f"prefab_count[{c['prefab']}]",
                            "want": c["count"], "got": got, "tolerance": tol,
                            "ok": abs(got - int(c["count"])) <= tol})

    if "absent" in exp:
        # A retire's postcondition.  Counting to zero is the ONLY honest proof
        # a demolition happened: `deleteObjects` echoes a line per object, so
        # a reply that looks empty is indistinguishable from a reply that was
        # truncated.
        for c in exp["absent"]:
            got, _ = srv.count(c["prefab"], c["pos"][0], c["pos"][1],
                               float(c["max"]))
            results.append({"check": f"absent[{c['prefab']}]", "want": 0,
                            "got": got, "ok": got == 0})

    if "terrain_compiler" in exp:
        # One compiler per zone, counted at the zone centre inside a radius
        # smaller than half a zone so neighbours cannot leak in.
        for entry in rec["params"]["entries"]:
            cx, cz = entry["centre"]
            got, _ = srv.count("_TerrainCompiler", cx, cz, 31.0, ignore="")
            results.append({"check": f"terrain_compiler{entry['zone']}",
                            "want": 1, "got": got, "ok": got == 1,
                            "why": "Heightmap::GetAndCreateTerrainCompiler "
                                   "returns the FIRST compiler in a zone, so 2 "
                                   "means one of them is dead weight and the "
                                   "terrain on screen is not the terrain "
                                   "written"})

    if "tag_readback" in exp:
        p = rec["params"]
        x, y, z = p["pos"]
        reply = srv.command(
            f"findObjects -prefab {p['prefab']} -near {x:.2f} {y:.2f} {z:.2f} "
            f"3.0 -detailed")
        tag = p["tag"]
        results.append({"check": "tag_readback", "want": tag,
                        "ok": (tag in reply) if not srv.dry else True,
                        "got": reply[:400],
                        "why": "spawn_object goes through consoleCommand, "
                               "whose reply is a fixed echo, so the tag is "
                               "read back FROM THE WORLD or not at all"})

    if not results:
        results.append({"check": "none", "ok": False,
                        "why": "the op declared an `expect` this driver cannot "
                               "measure; refusing to call it verified"})
    return results


# --------------------------------------------------------------------------
# Guards for non-idempotent ops
# --------------------------------------------------------------------------

def already_present(srv: Server, rec: dict) -> tuple[bool, dict]:
    """Is the op's output already in the world?

    This is what makes a resume safe for `spawn`, `spawn_plan` and `portal`,
    which DUPLICATE when re-run.  It is a measurement; when it cannot measure,
    the caller stops rather than risking a double-built house.
    """
    op, p = rec["op"], rec["params"]
    if op in ("spawn", "portal"):
        x, y, z = p["pos"]
        # MEASURED THE HARD WAY during the round-trip proof: a 2.0 m guard
        # radius saw the portal 2.0 m away as "this op's output already
        # present" and SKIPPED the second end of the pair.  The world then
        # held one portal of a pair -- the exact defect the pair invariant
        # exists to prevent -- and the replay refused at the next
        # postcondition.  The guard must be tighter than the closest spacing
        # between two objects of the same prefab, and since `spawn_object`
        # places at an EXACT position, the honest radius is an epsilon around
        # that position, not a neighbourhood.
        radius = float(p.get("guard_radius_m", GUARD_EPS_M))
        got, _ = srv.count(p["prefab"], x, z, radius)
        return got >= 1, {"probe": f"{p['prefab']} within {radius:g} m of "
                                   f"the recorded position",
                          "count": got}
    if op == "spawn_plan":
        a = p["anchor"]
        # Scope to the plan's own most common prefab: an id=* count at a built
        # site is the long answer that wedges the sink.
        prefab, want = max(p["prefabs"].items(), key=lambda kv: kv[1])
        reach = float(p.get("reach_m", 40.0))
        got, _ = srv.count(prefab, a["x"], a["z"], reach)
        return got >= want, {"probe": f"{prefab} within {reach:g} m of anchor",
                             "count": got, "want": want}
    return False, {"probe": "none"}


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------

def send_wire(srv: Server, wire: list[str]) -> list:
    """Send a record's literal commands, each by the transport its verb needs.

    THE BUILD PATH AND THE REPLAY PATH BOTH CALL THIS, and that is the point:
    if building used one transport and replaying another, the ledger would be
    a faithful record of commands that were never sent the way they are
    replayed, and the difference would not show up until a regeneration.
    """
    replies = []
    for cmd in wire:
        verb = cmd.split()[0] if cmd.split() else ""
        if verb in STAGED_VERBS:
            replies.append(_staged(srv, [cmd]))
        elif verb in NATIVE_VERBS:
            replies.append(srv.command(cmd))
        else:
            # Everything else is a Server Devcommands / World Edit Commands
            # verb and MUST go through the `consoleCommand` bridge.  MEASURED:
            # `spawn_object` sent as a bare RCON verb answers "Unknown command
            # spawn_object" -- ValheimRcon's own spawn verb is `spawn` and
            # takes no data payload.  Getting this wrong produces a run that
            # sends every line, reports a reply for each, and builds nothing.
            replies.append(srv.console(cmd))
    return replies


def apply_record(srv: Server, blobs: "BlobStore", rec: dict) -> dict:
    """Send one op's recorded wire, then measure its postcondition."""
    op = rec["op"]
    spec = schema.OPS[op]
    out: dict = {"seq": rec["seq"], "op": op, "actor": rec["actor"],
                 "role": rec["params"].get("role")}

    if not spec.mutating:
        out["status"] = "record-only"
        return out

    # Blobs the op needs must exist and must digest correctly BEFORE anything
    # is sent: a half-applied terrain write is worse than an unstarted one.
    for sha in rec.get("requires", {}).get("blobs", []):
        blobs.read(sha)

    if spec.idempotent == "guard":
        present, probe = already_present(srv, rec)
        out["guard"] = probe
        if present:
            out["status"] = "skipped-already-present"
            return out

    if op == "terrain_write":
        _materialise_data_entries(blobs, rec)

    out["replies"] = [str(r)[:200] for r in send_wire(srv, rec["wire"])]

    out["checks"] = check_expect(srv, rec)
    out["status"] = "applied" if all(c["ok"] for c in out["checks"]) \
        else "VERIFICATION FAILED"
    return out


def _staged(srv: Server, wire: list[str]) -> dict:
    """Upgrade World's staged operations need `start`, and they run across
    frames -- the answer immediately after `start` is not the answer.  MEASURED:
    querying too early made a 69-object pad look empty and the removal then ran
    against nothing."""
    out = {}
    for cmd in wire:
        srv.console("stop")
        out[cmd] = srv.console(cmd)
        out["start"] = srv.console("start")
    return {k: (v if isinstance(v, dict) else [str(x)[:120] for x in v])
            for k, v in out.items()}


def _materialise_data_entries(blobs: "BlobStore", rec: dict) -> None:
    """Write the World Edit Commands data entries a terrain_write's
    `spawn_object ... data=<entry>` refers to.

    The blob IS the TCData byte array; the YAML file is only its delivery
    vehicle, so it is regenerated from the blob rather than stored twice.  The
    data directory is watched (`ServerDevcommands.Yaml::SetupWatcher`), so the
    entry is live shortly after the write; the pause is for the watcher.
    """
    import base64
    import yaml

    data_dir = Path(os.environ.get(
        "WEC_DATA_DIR",
        "/media/big4/projects/game/valheim/Ulfsland/config_merged/bepinex/data"))
    doc = []
    for e in rec["params"]["entries"]:
        blob = blobs.read(e["blob_sha256"])
        doc.append({"name": e["data_entry"],
                    "bytes": ["TCData, " + base64.b64encode(blob).decode()]})
    data_dir.mkdir(parents=True, exist_ok=True)
    name = rec["params"]["name"].replace("-", "_")
    path = data_dir / f"terraform_{name}.yaml"
    path.write_text(yaml.safe_dump(doc, default_flow_style=False,
                                   width=10 ** 9, sort_keys=False))
    time.sleep(4)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

class Replay:
    def __init__(self, root: Path, *, dry: bool = False):
        self.root = root
        self.blobs = BlobStore(root / "blobs")
        self.chain = chain_report(root / "ledger.jsonl")
        self.records = self.chain["records"]
        if not self.records:
            raise LedgerError(f"{root}/ledger.jsonl is empty")
        if self.records[0]["op"] != "world_manifest":
            raise LedgerError("the first line must be the world_manifest")

        # STRICTNESS LIVES HERE, not in `open`.  Opening a log to APPEND and
        # opening it to REPLAY are different questions: a reader that refuses
        # to open a damaged log cannot even record that the log is damaged,
        # but a replay from an ambiguous order is not a replay of what was
        # built.  So appending tolerates a labelled wart and replaying does
        # not.
        undocumented = [f for f in self.chain["forks"] if not f["documented"]]
        if undocumented:
            raise Drift(
                f"the chain FORKS and the fork is not documented: "
                f"{json.dumps(undocumented, indent=1)}\n"
                f"Two branches carry the same `prev`, so the log records two "
                f"possible pasts and a replay cannot know which one built the "
                f"world. Append a `chain_reset` naming both digests, or fix "
                f"the log's producer. Nothing is guessed here.")
        if self.chain["forks"]:
            self.crossed_forks = self.chain["forks"]
        else:
            self.crossed_forks = []

        self.manifest = self.records[0]["params"]
        self.dry = dry
        self.progress_path = root / "replay.progress.json"

    # -- progress ----------------------------------------------------------

    def progress(self) -> dict:
        if self.progress_path.exists():
            return json.loads(self.progress_path.read_text("utf-8"))
        return {"ledger_head": self.chain["head"], "applied": {}, "preamble": None}

    def save_progress(self, prog: dict) -> None:
        prog["ledger_head"] = self.chain["head"]
        tmp = self.progress_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prog, indent=1))
        os.replace(tmp, self.progress_path)

    # -- preflight ---------------------------------------------------------

    def preflight(self, *, world: str | None = None,
                  probe_prefabs: bool = True) -> dict:
        world = world or self.manifest["world"]
        live = fingerprint(world, heavy=True)
        drift = compare_manifest(self.manifest, live)

        blobs = {"missing": [], "corrupt": [], "ok": 0}
        for rec in self.records:
            for sha in rec.get("requires", {}).get("blobs", []):
                try:
                    self.blobs.read(sha)
                    blobs["ok"] += 1
                except LedgerError as exc:
                    key = "missing" if "missing" in str(exc) else "corrupt"
                    blobs[key].append({"seq": rec["seq"], "sha": sha})

        bodies = {"unresolved": [], "mismatched": [], "ok": 0}
        for rec in self.records:
            body = rec["params"].get("body") if rec["op"] == "spawn_plan" else None
            if not body or "corpus_path" not in body:
                continue
            path = Path(body["corpus_path"])
            if not path.is_file():
                bodies["unresolved"].append({"seq": rec["seq"],
                                             "path": str(path)})
                continue
            from writer import sha256_file  # noqa: PLC0415
            got = sha256_file(path)
            if got != body["sha256"]:
                bodies["mismatched"].append({"seq": rec["seq"],
                                             "path": str(path),
                                             "recorded": body["sha256"],
                                             "live": got})
            else:
                bodies["ok"] += 1

        prefabs = sorted({p for rec in self.records
                          for p in rec.get("requires", {}).get("prefabs", [])})
        prefab_result: dict = {"checked": False, "missing": [],
                               "prefabs": prefabs}
        if probe_prefabs and prefabs and not self.dry:
            prefab_result["checked"] = True
            with Server(dry=False) as srv:
                for prefab in prefabs:
                    # `objects_count` on an unknown prefab answers with no
                    # Total; the absence IS the signal, so it is caught rather
                    # than raised.
                    try:
                        srv.count(prefab, 0.0, 0.0, 1.0)
                    except LedgerError:
                        prefab_result["missing"].append(prefab)

        fatal = [d for d in drift if d["level"] == "fatal"]
        report = {
            "root": str(self.root),
            "records": len(self.records),
            "ledger_head": self.chain["head"],
            "recorded_world": {k: self.manifest.get(k) for k in
                               ("world", "seed_name", "seed", "world_version",
                                "world_gen_version", "game_build_anchor")},
            "drift": drift,
            "blobs": blobs,
            "bodies": bodies,
            "prefabs": prefab_result,
            "verdict": "REFUSE" if (fatal or blobs["missing"] or
                                    blobs["corrupt"] or bodies["unresolved"] or
                                    bodies["mismatched"] or
                                    prefab_result["missing"]) else "PROCEED",
        }
        return report

    # -- plan --------------------------------------------------------------

    def plan(self) -> dict:
        prog = self.progress()
        done = set(int(k) for k in prog.get("applied", {}))
        pending = [(i, r) for i, r in enumerate(self.records)
                   if r["op"] in schema.MUTATING and i not in done]
        by_op: dict[str, int] = {}
        for _i, r in pending:
            by_op[r["op"]] = by_op.get(r["op"], 0) + 1
        return {"total": len(self.records), "already_applied": len(done),
                "pending": len(pending), "pending_by_op": by_op,
                "next_line": pending[0][0] if pending else None,
                "next_seq_label": pending[0][1]["seq"] if pending else None,
                "duplicate_seqs": self.chain["duplicate_seqs"],
                "preamble_done": bool(prog.get("preamble"))}

    # -- apply -------------------------------------------------------------

    def apply(self, *, first: int = 0, last: int | None = None,
              skip_preamble: bool = False) -> dict:
        pre = self.preflight(probe_prefabs=not self.dry)
        if pre["verdict"] == "REFUSE":
            raise Drift(
                "preflight REFUSED; nothing was sent.\n" +
                json.dumps({k: pre[k] for k in
                            ("drift", "blobs", "bodies", "prefabs")}, indent=1))

        prog = self.progress()
        if prog.get("ledger_head") != self.chain["head"] and prog.get("applied"):
            raise Drift(
                f"the progress file was written against ledger head "
                f"{prog.get('ledger_head')} but the ledger now heads at "
                f"{self.chain['head']}. The ledger changed under a partially "
                f"applied replay; resuming would apply a different build on "
                f"top of a half-built one.")

        if not skip_preamble and not prog.get("preamble") and not self.dry:
            prog["preamble"] = restart_and_wait(self.manifest["world"])
            self.save_progress(prog)

        applied = prog.setdefault("applied", {})
        results = []
        with Server(dry=self.dry) as srv:
            srv.probe()
            # BY FILE INDEX, NOT BY `seq`.  MEASURED: a concurrent-append
            # fork duplicated seqs 43-46 across two branches -- four
            # record-only notes on one, four VERIFIED spawns on the other.
            # Keyed by `seq`, a resume would have seen the notes' labels in
            # the progress file and silently skipped a ferry terminal, its
            # sign, its portal and a Longship, then reported success. File
            # position is the only total order the artefact has.
            for i, rec in enumerate(self.records):
                if i < first or (last is not None and i > last):
                    continue
                if rec["op"] not in schema.MUTATING:
                    continue
                if str(i) in applied:
                    continue
                res = apply_record(srv, self.blobs, rec)
                res["line"] = i
                results.append(res)
                if res["status"] == "VERIFICATION FAILED":
                    self.save_progress(prog)
                    raise Drift(
                        f"file line {i} (seq label {rec['seq']}, {rec['op']}, "
                        f"{rec['actor']}) was sent and its postcondition does "
                        f"NOT hold: {json.dumps(res['checks'], indent=1)}\n"
                        f"Stopping. The world is now partially built and the "
                        f"progress file records every earlier line as "
                        f"applied; fix the cause, then resume.")
                applied[str(i)] = {"status": res["status"],
                                   "seq_label": rec["seq"], "op": rec["op"],
                                   "ts": time.strftime("%FT%TZ",
                                                       time.gmtime())}
                self.save_progress(prog)
            if not self.dry:
                srv.command("save")
        return {"applied": len(results), "results": results,
                "commands_sent": len(srv.sent),
                "crossed_forks": self.crossed_forks}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["preflight", "plan", "apply"])
    ap.add_argument("--root", required=True)
    ap.add_argument("--from", dest="first", type=int, default=0,
                help="first FILE LINE to apply (not a seq: seq is an advisory label after a fork)")
    ap.add_argument("--to", dest="last", type=int,
                help="last FILE LINE to apply")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve, guard and print, send nothing")
    ap.add_argument("--skip-preamble", action="store_true",
                    help="the server has already been restarted since the "
                         "world was created and RCON is proven live")
    args = ap.parse_args()

    rp = Replay(Path(args.root), dry=args.dry_run)
    try:
        if args.op == "preflight":
            report = rp.preflight()
            print(json.dumps(report, indent=1))
            return 0 if report["verdict"] == "PROCEED" else 3
        if args.op == "plan":
            print(json.dumps(rp.plan(), indent=1))
            return 0
        print(json.dumps(rp.apply(first=args.first, last=args.last,
                                  skip_preamble=args.skip_preamble), indent=1))
        return 0
    except (Drift, LedgerError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
