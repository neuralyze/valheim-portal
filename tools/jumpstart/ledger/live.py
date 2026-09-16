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

try:
    from . import schema
    from .writer import Ledger, fingerprint
    from .schema import LedgerError
    from . import replay as R
except ImportError:  # run as a script
    import schema
    from writer import Ledger, fingerprint
    from schema import LedgerError
    import replay as R


class PostconditionFailed(RuntimeError):
    """The op was recorded and sent, and the world does not show its effect."""


class LiveBuilder:
    """One agent's live write session against one ledger."""

    def __init__(self, *, actor: str, world: str = "Ulfsland",
                 root: Path | None = None, dry: bool = False):
        self.actor = actor
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

    def __enter__(self) -> "LiveBuilder":
        self.srv = R.Server(dry=self.dry)
        self.srv.__enter__()
        # A `players` reply proves the main thread ran a frame.  The log
        # cannot: the heartbeat period is 600 s, so a quiet log and a dead
        # server look identical for ten minutes.
        self.srv.probe()
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
    ap.add_argument("--world", default="Ulfsland")
    args = ap.parse_args()
    led = Ledger(R.HERE / "runs" / args.world, actor="cli")
    print(json.dumps(led.close_report(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
