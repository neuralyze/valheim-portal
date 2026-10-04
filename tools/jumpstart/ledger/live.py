#!/usr/bin/env python3
"""The ONLY sanctioned way to write to the live world.

    from ledger.live import LiveBuilder

    with LiveBuilder(actor="RoadNet") as b:
        b.note("fords are RoadNet terrain, not Crossings structures")
        b.observe("span S1 water width", "MEASURED at 1 m from patchscan",
                  "tools/jumpstart/roads/survey.py", 41.5, units="m")
        b.emit("terrain_write", params={...}, wire=[...], expect={...})
        print(b.close())

WHY THIS EXISTS RATHER THAN EACH AGENT DRIVING RCON ITSELF.  Two of the
ledger's invariants -- the one-compiler-per-zone clobber check and
`flatten: "FORBIDDEN"` -- can only see operations that go through the ledger.
An ad-hoc `spawn_object` or a hand-run `objects_remove` is invisible to them,
so a bridge placed outside the ledger can be flattened by a road segment
inside it and the guard that exists precisely to prevent the early-dock defect
sits there unfired while it happens.  Routing every write through one object
is what makes those guards real instead of decorative.

WHAT `emit` DOES, IN THIS ORDER, and every step earns its place:

  1. APPEND FIRST.  The record is validated (which is where a missing
     `expect`, a clobbered zone, a duplicate portal tag or an unmodelled
     param is refused) and fsync'd to the chain BEFORE anything is sent.  A
     record of something that may not have happened is recoverable: the
     replay's guard measures before it acts.  An unlogged mutation is not
     reproducible at all, and that is the failure this artefact exists to
     prevent.
  2. SEND THROUGH `replay.send_wire`.  The SAME function the replay driver
     uses, so the transport that built the world is by construction the
     transport that rebuilds it.  Three transports exist and picking the wrong
     one fails silently.
  3. VERIFY THE POSTCONDITION with the SAME `replay.verify_expect`, which
     WAITS for a staged operation to finish rather than measuring once.  If it does
     not hold, `emit` raises.  An operation that appeared to succeed is the
     single most expensive defect class this project has.

Nothing here repairs anything.  A failed postcondition leaves the record in
the ledger and the world in whatever state it reached; the caller fixes the
cause and either re-emits or records a `retire`.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "terraform"))
if str(HERE.parent) not in sys.path:
    sys.path.append(str(HERE.parent))
import worldseed  # noqa: E402

try:
    from . import schema
    from .writer import Ledger, fingerprint
    from .schema import LedgerError
    from . import replay as R
    from . import writer as W
except ImportError:  # run as a script
    import schema
    from writer import Ledger, fingerprint
    from schema import LedgerError
    import replay as R
    import writer as W


# ---------------------------------------------------------------------------
# THE LOADED-ZONE GUARD
# ---------------------------------------------------------------------------
# A `terrain_write` is `deleteObjects -zone ZX ZZ -prefab _TerrainCompiler
# -force` FOLLOWED BY a spawn.  That is safe against a zone only the server
# holds, and it is a RACE against a zone a CLIENT holds: a peer inside
# `ZoneSystem.m_activeArea` owns those ZDOs, keeps the old compiler in its own
# copy of the zone, and pushes it back after the delete -- so the record says
# the ground moved and the world keeps the surface it had.  Nothing downstream
# can tell that apart from a write that simply did not land: the postcondition
# `verify_expect` asks is "is there a _TerrainCompiler in this zone", and after
# a lost race there IS one.  It is the client's.
#
# MEASURED tonight, and it is why this exists rather than being a precaution:
# `players` returned one peer at (-1032.52, 37.55, 1641.07), zone (-16 26),
# HP 0/25, standing on R4-vestvik-harbour's B approach -- which is EXACTLY the
# one zone the R4 B cut-only disposal names.  Eight vertices, 0.5156 m3, one
# zone, and the only zone in the write was occupied.
#
# Every terrain emitter in this tree already printed `players` into its own
# `meta`, which is PROVENANCE and not a GUARD: it records who was standing
# there while the write went out.  The ONE sanctioned write path is the only
# place the reading can refuse.

# `ZoneSystem.m_activeArea = 1`: a peer loads its own zone AND the ring around
# it, so one player's LOADED SET is the 3x3 of `zone_of(x, z)`, not one zone.
ACTIVE_AREA_ZONES = 1

_PLAYER_LINE = __import__("re").compile(
    r"Position:\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)"
    r"(?:\s*Zone:\s*\(\s*(-?\d+)\s+(-?\d+)\s*\))?")
_ONLINE_LINE = __import__("re").compile(r"^\s*Online\s+(\d+)\s*$",
                                        __import__("re").M)


def parse_players(text: str) -> dict:
    """The `players` reply as a POPULATION, not a list.

    `declared` is what the header says is online; `read` is how many of those
    yielded a position.  They are reported separately and BOTH are needed: a
    roster that says one peer is online and yields no position is UNREADABLE,
    and an unreadable roster that is treated as an empty one is a guard that
    opens precisely when it cannot see.
    """
    import tcdata                                          # noqa: PLC0415
    m = _ONLINE_LINE.search(text or "")
    declared = int(m.group(1)) if m else None
    rows = []
    for line in (text or "").splitlines():
        hit = _PLAYER_LINE.search(line)
        if not hit:
            continue
        x, y, z = (float(hit.group(i)) for i in (1, 2, 3))
        # `x y z` in a Valheim position is WORLD x, HEIGHT, WORLD z.
        ours = tcdata.zone_of(x, z)
        theirs = ((int(hit.group(4)), int(hit.group(5)))
                  if hit.group(4) is not None else None)
        rows.append({
            "name": line.split()[0] if line.split() else "?",
            "pos": [x, y, z],
            "zone": list(ours),
            "zone_as_the_server_printed_it": (list(theirs) if theirs
                                              else None),
            # THE TWO DERIVATIONS MUST AGREE.  `tcdata.zone_of` is this
            # tree's own `ZoneSystem::GetZone`, and the server prints its
            # own.  A disagreement means one of the two is wrong about
            # where a peer is, and a guard cannot pick a side.
            "zone_agrees": theirs is None or theirs == ours,
        })
    return {"declared_online": declared, "read": len(rows), "players": rows,
            "raw": (text or "").strip()[:400]}


def loaded_zones(roster: dict, active_area: int = ACTIVE_AREA_ZONES) -> dict:
    """zone index -> the player names holding it."""
    held: dict[tuple[int, int], list[str]] = {}
    for p in roster["players"]:
        zx, zz = p["zone"]
        for dx in range(-active_area, active_area + 1):
            for dz in range(-active_area, active_area + 1):
                held.setdefault((zx + dx, zz + dz), []).append(p["name"])
    return held


def zones_of_record(rec: dict) -> list[tuple[int, int]]:
    """The zone set a record's compilers name, BY INDEX."""
    return sorted({(int(e["zone"][0]), int(e["zone"][1]))
                   for e in (rec.get("params") or {}).get("entries", [])
                   if e.get("zone")})


def loaded_zone_check(rec: dict, players_text: str,
                      active_area: int = ACTIVE_AREA_ZONES) -> dict:
    """Does this write's zone set INTERSECT the set a client currently holds?

    Judged BY INDEX and reported as an INTERSECTION, because that is the only
    form another agent can veto against.  Prints the size of the population it
    judged AND the size of the set it was drawn from, both sides.
    """
    roster = parse_players(players_text)
    zones = zones_of_record(rec)
    held = loaded_zones(roster, active_area)
    disagree = [p for p in roster["players"] if not p["zone_agrees"]]
    unreadable = (roster["declared_online"] is None
                  or roster["read"] < roster["declared_online"])
    hits = sorted(set(zones) & set(held))
    out = {
        "zones_named_by_the_write": [list(z) for z in zones],
        "zones_named": len(zones),
        "players_declared_online": roster["declared_online"],
        "players_whose_position_was_read": roster["read"],
        "active_area_zones": active_area,
        "zones_held_by_a_client": len(held),
        "intersection": [list(z) for z in hits],
        "intersecting_zones": len(hits),
        "occupants": {f"{z[0]},{z[1]}": sorted(set(held[z])) for z in hits},
        "players": roster["players"],
        "population": (
            f"the {len(zones)} zone(s) this terrain_write names, judged "
            f"against the {len(held)} zone(s) held by the "
            f"{roster['read']} of "
            f"{roster['declared_online'] if roster['declared_online'] is not None else '?'} "
            f"online peer(s) whose position could be read, at an active area "
            f"of {active_area} zone(s) in each direction"),
    }
    if unreadable:
        out["verdict"] = "REFUSED"
        out["why"] = (
            f"the `players` roster is UNREADABLE: it declares "
            f"{roster['declared_online']} online and "
            f"{roster['read']} position(s) could be parsed. A roster that "
            f"cannot be read is not an empty one, and a guard that opens "
            f"when it cannot see is not a guard.")
    elif disagree:
        out["verdict"] = "REFUSED"
        out["why"] = (
            f"{len(disagree)} of {roster['read']} peer(s) are in a different "
            f"zone by `tcdata.zone_of` than the server printed: "
            f"{[(p['name'], p['zone'], p['zone_as_the_server_printed_it']) for p in disagree]}. "
            f"Two authorities for one peer's zone and no way to pick a side.")
    elif hits:
        out["verdict"] = "REFUSED"
        out["why"] = (
            f"{len(hits)} of the {len(zones)} zone(s) this write names "
            f"{out['intersection']} are LOADED BY A CLIENT "
            f"({out['occupants']}). A terrain_write deletes the zone's "
            f"_TerrainCompiler before it spawns the new one, and a peer "
            f"inside the active area owns those ZDOs and will push its own "
            f"copy back -- so the record would say the ground moved while "
            f"the world kept the surface it had, and the postcondition "
            f"would still find a compiler in the zone, because it would "
            f"find the CLIENT'S. Move the peer out or wait for the zone to "
            f"unload; there is no waiver.")
    else:
        out["verdict"] = "CLEAR"
        out["why"] = (
            f"none of the {len(zones)} zone(s) this write names is inside "
            f"the {len(held)} zone(s) any of {roster['read']} peer(s) hold")
    return out


class PostconditionFailed(RuntimeError):
    """The op was recorded and sent, and the world does not show its effect."""


class LiveBuilder:
    """One agent's live write session against one ledger."""

    # LEDGER_WORLD_DEPENDS_ON: the WORLD BEING WRITTEN, and nothing else.
    #
    # This parameter was `world: str = "Ulfsland"` -- a CONSTANT standing for
    # a quantity that depends on which world the session is driving, i.e. a
    # default that names a SUPERSET of one world.  MEASURED 2026-09-23: of
    # the 40 `LiveBuilder(` callsites in this tree, 36 pass no world at all,
    # so `roads/precensus.py survey --actor CensusRun` against the fresh seed
    # Fresh42 would have opened `ledger/runs/Ulfsland/ledger.jsonl` and
    # appended 295 `zones_generate` records for a world it was not surveying,
    # into the one ledger this project holds untouchable.  A silent default
    # is the only thing that stood between a fresh-seed census and that.
    #
    # `worldseed.world()` is the tree's ONE resolver for "which world is this
    # run driving" (argument > $VH_WORLD > "Ulfsland"), so this changes NO
    # existing caller's behaviour -- with $VH_WORLD unset it still resolves
    # Ulfsland -- while a run that has declared its world now reaches that
    # world's own ledger without 36 edits.
    def __init__(self, *, actor: str, world: str | None = None,
                 root: Path | None = None, dry: bool = False):
        # THE ACTOR IS THE CALLER'S, ALWAYS, AND IT IS KEYWORD-ONLY SO IT
        # CANNOT BE DEFAULTED BY ACCIDENT.  A module-level `ACTOR` constant is
        # a default in disguise: `clear.py`'s said `RoadClear` while `RoadEmit`
        # was running it, and the ledger then recorded work under the name of
        # an agent that had yielded an hour earlier.  Provenance that is wrong
        # is worse than provenance that is missing, because it reads as
        # evidence.
        if not actor or not isinstance(actor, str):
            raise LedgerError("LiveBuilder needs the actor that is actually "
                              "running: the log is the only place a defect's "
                              "provenance can be found")
        self.actor = actor
        world = world or worldseed.world()
        self.world = world
        self.dry = dry
        self.root = Path(root) if root else (R.HERE / "runs" / world)
        self.led = Ledger.open(world, actor=actor, root=self.root,
                               manifest=fingerprint(world, heavy=True))
        # `Ledger.open` only writes the manifest when the ledger is new, but
        # the actor must be THIS agent for the records it appends -- siblings
        # share one ledger and the log has to say who did what.
        self.led.actor = actor
        self.srv: R.Server | None = None
        # UNSET UNTIL `__enter__` READS IT, and stated as unset rather than
        # defaulted to agreement.
        self.transport: dict = {"world": "", "declared": False,
                                "how": "no session has been opened"}

    # TRANSPORT_AGREEMENT_DEPENDS_ON: the world this session RECORDS INTO and
    # the world the socket it is about to drive belongs to.  THEY WERE NEVER
    # COMPARED.  MEASURED 2026-09-24: `pipeline.Scope.env()` publishes
    # `VH_SEED` and `VH_WORLD` and nothing else; `terraform/rcon.py` keyed the
    # container on `$VALHEIM_WORLD` else the literal "Ulfsland"; and
    # `replay.Server.__enter__` builds `Rcon(timeout=30.0)` with no config.
    # So `buildstage.py run --world Fresh42` with no transport override would
    # have RECORDED INTO FRESH42'S LEDGER AND SENT EVERY COMMAND TO
    # `valheim-server-Ulfsland` -- the record and the ground disagreeing with
    # nothing in the tree able to notice.  ONE QUANTITY, TWO INDEPENDENT ENV
    # KEYS, AND NOTHING COMPARED THEM.
    #
    # VERDICT 2026-09-24, LATENT NOT LIVE, measured against the WORLD and not
    # the ledger: Ulfsland's save is byte-identical to the 2026-09-23 freeze
    # in 38 of 40 files, the two exceptions being the documented drift chunks
    # 22_1c (+55 ZDOs) and 22_1e (+57), written 75 minutes AFTER the Fresh42
    # build's last record; no new chunk file appeared; and Fresh42's own save
    # closed at 17:17:10Z, one second after its own closing `save` record.
    # The path existed and was never taken.  This refuses it anyway.
    #
    # A SOCKET IS NOT A WORLD.  `$VALHEIM_RCON_HOST` names an address, and
    # `transport_scope()["declared"]` is False when that is all anyone said.
    # An undeclared transport is NOT refused -- a fresh seed's bare server is
    # addressed exactly that way and refusing it would forbid every fresh
    # build -- it is RECORDED, on every record this session appends, so the
    # question "which world did this actually reach" is answerable from the
    # chain instead of from four forensic measurements of the save.
    def __enter__(self) -> "LiveBuilder":
        if self.dry:
            self.transport = {"world": self.world,
                              "how": "dry run: no socket is opened",
                              "declared": True}
        else:
            from rcon import transport_scope  # noqa: PLC0415 -- terraform/
            self.transport = transport_scope()
        from rcon import transport_disagreement  # noqa: PLC0415
        why = transport_disagreement(self.world, self.transport)
        if why:
            raise LedgerError(why)
        self.srv = R.Server(dry=self.dry)
        self.srv.__enter__()
        # A `players` reply proves the main thread ran a frame.  The log
        # cannot: the heartbeat period is 600 s, so a quiet log and a dead
        # server look identical for ten minutes.
        self.srv.probe()
        # AND THE CONSOLE MUST BE ABLE TO RUN THE VERBS THIS SESSION EMITS.
        # `Terminal.m_cheat` is FALSE in a freshly started server and every
        # Upgrade World verb is registered IsCheat=true, so a write session
        # opened without this reports a successful RCON echo for a command
        # the game refused.  Probed rather than sent, because `devcommands`
        # is a toggle -- see `replay.Server.require_cheats`.
        self.cheats = self.srv.require_cheats()
        # AND CHEATS ARE ONLY HALF THE PRECONDITION.  A verb the server has
        # no plugin for is REFUSED by `Terminal.TryRunCommand` with the same
        # successful RCON echo -- MEASURED on Fresh42, 13 pads rc 0 and zero
        # terrain written because `spawn_object` was unregistered.  See
        # `replay.Server.require_verbs`.
        self.verbs = self.srv.require_verbs()
        return self

    def __exit__(self, *exc) -> None:
        if self.srv:
            self.srv.__exit__(*exc)

    # -- blobs -------------------------------------------------------------

    def blob(self, data: bytes, *, note: str = "") -> str:
        return self.led.blob(data, note=note)

    def blob_file(self, path: Path, *, note: str = "") -> str:
        return self.led.blob_file(path, note=note)

    # -- the one write verb ------------------------------------------------

    def emit(self, op: str, *, params: dict, wire: list[str] | None = None,
             requires: dict | None = None, expect: dict | None = None,
             meta: dict | None = None) -> dict:
        # A DRY RUN DOES NOT APPEND, AND THE FACT THAT IT USED TO IS A DEFECT
        # THAT BIT TWICE IN ONE SESSION.  `dry=True` exists so an agent can
        # test a gate, a wire string or a schema shape WITHOUT touching the
        # shared artefact -- which is exactly the moment a stray record is
        # most expensive, because the thing being tested is by definition not
        # yet trusted.  MEASURED tonight: `clear.py emit --dry`, run only to
        # find out whether the POI gate would refuse, left `observe` seq 933
        # in the chain under `RoadClear`'s name for work `RoadEmit` was doing,
        # so the log now attributes an action to an agent that had already
        # yielded.  The record is annotated by a later append rather than
        # edited, and this is the fix for the cause.
        #
        # The record is still BUILT and VALIDATED, because that is the useful
        # half of a dry run: `schema.validate` is where a missing `expect`, a
        # clobbered zone or an unmodelled param is caught.  It is simply not
        # written, not sent and not verified.
        if self.dry:
            rec = self.led.build(op, params=params, wire=wire,
                                 requires=requires, expect=expect, meta=meta)
            bad = schema.validate(rec)
            return {"seq": None, "op": op, "role": params.get("role"),
                    "status": "dry-run: validated, NOT appended, NOT sent",
                    "schema_problems": bad,
                    "wire": rec.get("wire") or []}
        # ---- THE LOADED-ZONE GUARD, BEFORE THE APPEND ------------------
        # This is the ONE refusal in `emit` that runs before the record is
        # written, and it earns that position: every other failure here
        # leaves a record of something that MAY have happened, which is
        # recoverable.  A write into a zone a client holds leaves a record of
        # something that DID NOT happen and CANNOT BE DETECTED afterwards,
        # because the postcondition finds the client's own compiler and calls
        # it ours.  An undetectable false record is worse than no record.
        #
        # It reads `players` ITSELF rather than trusting the caller's, so the
        # reading is immediately before the record by construction and not by
        # each emitter remembering to do it.
        if op == "terrain_write" and self.srv is not None:
            roster_text = self.srv.command("players")
            lz = loaded_zone_check({"params": params}, roster_text)
            print(f"loaded-zone guard: {lz['verdict']} -- {lz['population']}; "
                  f"{lz['intersecting_zones']} intersecting")
            meta = dict(meta or {})
            meta["loaded_zone_guard"] = {k: v for k, v in lz.items()
                                         if k != "players"}
            meta["players_before"] = roster_text.strip()[:400]
            if lz["verdict"] == "REFUSED":
                raise LedgerError(
                    f"REFUSED before the append: {lz['why']} "
                    f"Population: {lz['population']}.")
        # THE TRANSPORT, ON THE RECORD.  A record that cannot say which server
        # it reached is a record that cannot answer "did this land in the
        # right world", and answering that took four forensic measurements of
        # a save on 2026-09-24.  `declared: false` is written OUT LOUD when
        # `$VALHEIM_RCON_HOST` names a socket and nobody named a world -- an
        # ABSENCE STATED IS EVIDENCE; an absence omitted reads as agreement.
        # See TRANSPORT_AGREEMENT_DEPENDS_ON at `__enter__`.
        meta = dict(meta or {})
        meta["transport"] = {**self.transport, "ledger_world": self.world}
        # WHICH BUILD STEP WROTE THIS, stamped by the tool that ran it rather
        # than guessed from the role afterwards.  `buildstage.py` exports the
        # running step and unit; a record written outside a build carries
        # neither.  MEASURED on Fresh42's first drive: 60 settlement
        # guideposts carry role `waypoint_sign`, which the replay mapped to
        # step 7 roadside by role alone, although step 5 wrote them -- so 15
        # settlements read as roads owed a corridor recheck.
        import os as _os  # noqa: PLC0415
        if _os.environ.get("VH_BUILD_STEP"):
            meta["build_step"] = _os.environ["VH_BUILD_STEP"]
            meta["build_unit"] = _os.environ.get("VH_BUILD_UNIT", "")
        rec = self.led.append(op, params=params, wire=wire, requires=requires,
                              expect=expect, meta=meta)
        spec = schema.OPS[op]
        if not spec.mutating:
            return {"seq": rec["seq"], "op": op, "status": "record-only"}

        if self.srv is None:
            raise LedgerError("LiveBuilder.emit outside a `with` block: the "
                              "record would be written and never sent")

        for sha in (requires or {}).get("blobs", []):
            self.led.read_blob(sha)
        if op == "terrain_write":
            R._materialise_data_entries(self.led, rec)

        replies = R.send_wire(self.srv, rec["wire"])
        # THE SHARED VERIFY, not a bare `check_expect`.  A staged Upgrade
        # World op runs across frames, so measuring once immediately after
        # `start` reports work the world actually did as failed -- MEASURED
        # live, twice, and it aborted a road segment after three good
        # batches.  Build and replay must measure a staged op the same way
        # for the same reason they must SEND it the same way.
        checks, settled = R.verify_expect(self.srv, rec)
        failed = [c for c in checks if not c["ok"]]
        out = {"seq": rec["seq"], "op": op, "role": params.get("role"),
               "replies": [str(r)[:200] for r in replies], "checks": checks,
               "status": "applied" if not failed else "POSTCONDITION FAILED"}
        if settled is not None:
            out["settled_after_s"] = settled
        if failed:
            raise PostconditionFailed(
                f"seq {rec['seq']} ({op}, {self.actor}) was recorded and sent "
                f"and its postcondition does NOT hold: {failed}. The record "
                f"stays in the ledger -- fix the cause and re-emit, or record "
                f"a `retire`. Nothing is repaired for you.")
        return out

    # -- record-only conveniences -----------------------------------------

    def observe(self, what: str, method: str, tool: str, value,
                **extra) -> dict:
        """A measurement WITH ITS PROVENANCE.  `method` must begin MEASURED or
        INFERRED and `tool` must name the script; both are enforced by the
        schema, because a number whose origin is a transcript cannot be told
        apart from one computed off another agent's clobbered global."""
        return self.emit("observe", params={
            "what": what, "method": method, "tool": tool, "value": value,
            **extra})

    def note(self, text: str, **extra) -> dict:
        return self.emit("note", params={"text": text, **extra})

    # -- close -------------------------------------------------------------

    def close(self) -> dict:
        return self.led.close_report()


def main() -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("op", choices=["status"])
    worldseed.add_arguments(ap)
    args = ap.parse_args()
    seed, world = worldseed.apply(args)
    root = R.HERE / "runs" / world
    # The run's OWN seq-0 manifest decides whether these records belong to
    # this world, not the directory name this line just built out of it.
    W.require_run_scope(root, world, seed)
    led = Ledger(root, actor="cli")
    print(json.dumps(led.close_report(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
