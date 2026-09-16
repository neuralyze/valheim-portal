#!/usr/bin/env python3
"""The append-only writer, the blob store, and the environment fingerprint.

    from ledger import Ledger
    led = Ledger.open("Ulfsland", actor="RoadNet")
    sha = led.blob(tcdata_bytes, note="segment-04 zone -12,7")
    led.append("terrain_write", params={...}, wire=[...],
               requires={"mods": [...], "blobs": [sha]},
               expect={"zone_ctrl": 9}, meta={...})

Append is ATOMIC AND DURABLE per record: the line is written and fsync'd
before `append` returns, and the chain head is recomputed from what is on
disk.  A replay driver that crashes mid-build must be able to trust that the
last line it can read is a line that really happened; a buffered writer that
loses the tail turns "resume from seq N" into "build seq N twice".

Layout, beside this module in `runs/<world>/`:

    ledger.jsonl        the chain
    blobs/<sha256>      content-addressed payloads (plans, TCData, lattices)
    blobs/index.json    digest -> {bytes, note, first_seen_seq}

Blobs are content-addressed, so the same plan written twice costs one file and
a `spawn_plan` replayed against a changed plan file is caught by digest rather
than by date.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Importable both as `ledger.writer` (package) and as `./writer.py` (script);
# without this the two paths produce two distinct `schema` module objects and
# the op registry silently forks.
try:
    from . import schema
    from .schema import LedgerError
except ImportError:  # run as a script
    import schema
    from schema import LedgerError

REPO = HERE.parent.parent.parent
RUNS = HERE / "runs"

VALHEIM_ROOT = Path(os.environ.get(
    "VALHEIM_ROOT", "/media/big4/projects/game/valheim"))


# --------------------------------------------------------------------------
# Environment fingerprint
# --------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_fwl(path: Path) -> dict:
    """Seed, version and generator out of a `.fwl2` header.

    MEASURED against Ulfsland's `_main.1.fwl2`: a 4-byte length then a
    ZPackage of int32 version, string name, string seedName, int32 seed,
    int64 uid, int32 worldGenVersion.  Strings are ZPackage's 7-bit-length-
    prefixed UTF-8.

    This is read rather than assumed because it is the cheapest possible
    "am I pointed at the right world" check, and a replay pointed at the wrong
    world should stop at line one rather than at zone four hundred.
    """
    b = path.read_bytes()
    off = 4  # outer byte-array length
    version, = struct.unpack_from("<i", b, off)
    off += 4

    def zstr(o: int) -> tuple[str, int]:
        n, shift = 0, 0
        while True:
            byte = b[o]
            o += 1
            n |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        return b[o:o + n].decode("utf-8"), o + n

    name, off = zstr(off)
    seed_name, off = zstr(off)
    seed, = struct.unpack_from("<i", b, off)
    off += 4
    uid, = struct.unpack_from("<q", b, off)
    off += 8
    gen, = struct.unpack_from("<i", b, off)
    off += 4
    # MEASURED on `_main.1.fwl2`: one flag byte, then an int32 count and that
    # many strings -- the `-modifier` set the world was created with
    # ("resourcerate 200", "teleportall", "preset combat_default:...").
    # WORTH THE PARSE: `ZoneSystem::Save` strips every key resolving to a
    # named `GlobalKeys` member (gk < 41) BEFORE writing, and
    # `SetStartingGlobalKeys` re-derives them each boot from the launch args,
    # so the modifier set is NEVER in the db save and an empty global-key list
    # there is correct rather than corrupt.  `resources_muchmore` and
    # `portals_casual` change what the built world MEANS even when every
    # coordinate matches, so the ledger has to carry them from somewhere -- and
    # a live `globalKeys` observation is the better source, this the fallback.
    modifiers: list[str] = []
    try:
        off += 1
        count, = struct.unpack_from("<i", b, off)
        off += 4
        for _ in range(count):
            s, off = zstr(off)
            modifiers.append(s)
    except (struct.error, IndexError, UnicodeDecodeError):
        modifiers = []
    return {"world_version": version, "name": name, "seed_name": seed_name,
            "seed": seed, "uid": uid, "world_gen_version": gen,
            "modifiers": sorted(modifiers), "modifiers_source": "fwl2",
            "fwl_sha256": hashlib.sha256(b).hexdigest(), "fwl_path": str(path)}


def live_modifiers(timeout: float = 15.0) -> dict:
    """The modifier set as the RUNNING server reports it.

    Preferred over the `.fwl2` because it is what the process actually applied:
    the launch args can differ from the file that created the world, and it is
    the applied set that decides whether ore travels through a portal.
    Returns `{"modifiers": [...], "modifiers_source": "globalKeys"}` or an
    `error` -- never a silent empty list, because "not measured" and "no
    modifiers" are different facts.
    """
    try:
        sys.path.insert(0, str(HERE.parent / "terraform"))
        from rcon import Rcon  # noqa: PLC0415
        with Rcon(timeout=timeout) as rc:
            reply = rc.command("globalKeys")
        # MEASURED: the reply opens with a literal "Global Keys:" header line
        # before the keys themselves. Keeping it would make the modifier set
        # compare equal for the wrong reason on a server that returned only
        # the header.
        keys = sorted(ln.strip() for ln in reply.splitlines()
                      if ln.strip() and ln.strip() != "Global Keys:")
        return {"modifiers": keys, "modifiers_source": "globalKeys"}
    except Exception as exc:  # noqa: BLE001
        return {"modifiers": None, "modifiers_source": "unavailable",
                "error": f"{type(exc).__name__}: {exc}"}


def mod_inventory(plugins: Path) -> list[dict]:
    """Every plugin DLL, by relative path and digest.

    The mod set is not decoration: `ZoneSystem::PlaceLocations` runs whatever
    locations the loaded mods registered, so a replay on a different mod set
    generates a different world under the same seed.  Hashing the DLLs is the
    only statement of "the same mods" that survives a version string being
    wrong.
    """
    if not plugins.is_dir():
        return []
    out = []
    for dll in sorted(plugins.rglob("*.dll")):
        try:
            out.append({"path": str(dll.relative_to(plugins)),
                        "bytes": dll.stat().st_size,
                        "sha256": sha256_file(dll)})
        except OSError as exc:  # a mid-deploy read is a fact, not a crash
            out.append({"path": str(dll.relative_to(plugins)),
                        "error": str(exc)})
    return out


def mods_digest(mods: list[dict]) -> str:
    """One digest over the whole mod set, so drift is a single comparison."""
    payload = json.dumps([[m.get("path"), m.get("sha256")] for m in mods],
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def tool_fingerprint() -> dict:
    """Which code produced this ledger.  A dirty tree is recorded as dirty:
    a replay that cannot be traced to a commit is a replay whose generator
    cannot be re-run, and pretending otherwise is the drift we are trying to
    detect."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(REPO), *args],
                                  capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except Exception:
            return ""
    status = git("status", "--porcelain")
    return {"repo": str(REPO), "commit": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(status),
            "dirty_files": len(status.splitlines()),
            "schema_version": schema.SCHEMA_VERSION,
            "python": platform.python_version()}


def fingerprint(world: str = "Ulfsland", *, game_build_anchor: str | None = None,
                heavy: bool = True, probe_modifiers: bool = True) -> dict:
    """The `world_manifest` payload, measured from the host.

    `heavy=False` skips hashing 120 DLLs, for a caller that only wants the
    world identity.  A manifest written with heavy=False records that, so a
    replay does not mistake "not measured" for "no mods".
    """
    base = VALHEIM_ROOT / world
    worlds = base / "config_merged" / "worlds_local" / world
    fwls = sorted(worlds.glob("_main.*.fwl2")) if worlds.is_dir() else []
    wf = read_fwl(fwls[-1]) if fwls else {}
    dbs = sorted(worlds.glob("_main.*.db2")) if worlds.is_dir() else []
    plugins = base / "config_merged" / "bepinex" / "plugins"
    mods = mod_inventory(plugins) if heavy else []

    anchor_file = base / "mods" / ".game-build-anchor"
    anchor = game_build_anchor
    if anchor is None and anchor_file.is_file():
        # `mods/.game-build-anchor` is key=value lines written by
        # hostops/anchor_game_build.sh; the build identity is
        # `game_assembly_sha256`, and the first token of the file is
        # `anchor_version=1`, which is emphatically not it.
        for line in anchor_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "game_assembly_sha256":
                anchor = value.strip()
                break
    if anchor is None:
        anchor = schema.GAME_BUILD_ANCHOR_DEFAULT

    # Live first, file second, and the payload SAYS which -- because "the
    # save is empty of modifiers" is correct behaviour, not evidence of none.
    mods_applied = live_modifiers() if probe_modifiers else {
        "modifiers": None, "modifiers_source": "not probed"}
    if mods_applied.get("modifiers") is None:
        mods_applied = {"modifiers": wf.get("modifiers"),
                        "modifiers_source": wf.get("modifiers_source", "none"),
                        "probe_error": mods_applied.get("error")}
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "world": world,
        "seed_name": wf.get("seed_name", schema.ULFSLAND["seed_name"]),
        "seed": wf.get("seed", schema.ULFSLAND["seed"]),
        "world_version": wf.get("world_version", schema.ULFSLAND["world_version"]),
        "world_gen_version": wf.get("world_gen_version",
                                    schema.ULFSLAND["world_gen_version"]),
        "game_build_anchor": anchor,
        "modifiers": mods_applied,
        "mods": {"count": len(mods), "digest": mods_digest(mods) if mods else None,
                 "measured": heavy, "plugins_dir": str(plugins),
                 "dlls": mods},
        "tool": tool_fingerprint(),
        "fwl_sha256": wf.get("fwl_sha256"),
        "db_bytes": dbs[-1].stat().st_size if dbs else None,
        "host": platform.node(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def retired_seqs(records: list[dict]) -> set[int]:
    """Every seq a later `retire` removed.

    The ledger is the world's HISTORY, and replaying it reproduces the world --
    including the demolitions.  So an op that was later retired still replays
    (the order matters: something may have stood on it) and the retire replays
    after it, leaving the world without the object.  What must NOT happen is
    the ledger going on asserting a live portal pair for two ends that were
    deleted an hour ago; the refusal message a future site would get for a tag
    freed long ago is baffling, and a baffling refusal is a defect.
    """
    out: set[int] = set()
    for r in records:
        if r["op"] == "retire":
            out.update(int(s) for s in r["params"].get("retires", []))
    return out


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------

class Ledger:
    """Append-only, hash-chained, validated on the way in.

    Validation is at WRITE time on purpose.  A malformed op discovered at
    replay time is discovered on a fresh world at 3am; discovered at write
    time it is a traceback in the agent that caused it, while that agent is
    still running and still knows what it meant.
    """

    def __init__(self, root: Path, actor: str):
        self.root = root
        self.actor = actor
        self.path = root / "ledger.jsonl"
        self.blobs = root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self._index_path = self.blobs / "index.json"
        self._seq, self._head = self._replay_chain()

    # -- construction ------------------------------------------------------

    @classmethod
    def open(cls, world: str = "Ulfsland", *, actor: str,
             root: Path | None = None, manifest: dict | None = None,
             heavy_fingerprint: bool = True) -> "Ledger":
        root = root or (RUNS / world)
        root.mkdir(parents=True, exist_ok=True)
        led = cls(root, actor)
        if led._seq < 0:
            led._append_raw("world_manifest",
                            manifest or fingerprint(world, heavy=heavy_fingerprint),
                            wire=[], requires={}, expect=None,
                            meta={"why": "seq 0 pins the world and the "
                                         "environment; replay refuses on "
                                         "mismatch rather than substituting"})
        return led

    # -- chain -------------------------------------------------------------

    def _replay_chain(self) -> tuple[int, str]:
        if not self.path.exists():
            return -1, schema.GENESIS_PREV
        with self.path.open("r", encoding="utf-8") as fh:
            return schema.chain_head(fh)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(ln) for ln in
                self.path.read_text("utf-8").splitlines() if ln.strip()]

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def head(self) -> str:
        return self._head

    # -- blobs -------------------------------------------------------------

    def blob(self, data: bytes, *, note: str = "") -> str:
        """Store bytes, return their sha256.  Content-addressed, so storing
        the same plan twice costs one file and a changed plan is a new digest
        rather than a silently overwritten one."""
        sha = hashlib.sha256(data).hexdigest()
        target = self.blobs / sha
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        index = self.blob_index()
        if sha not in index:
            index[sha] = {"bytes": len(data), "note": note,
                          "first_seen_seq": self._seq + 1}
            self._index_path.write_text(json.dumps(index, indent=1, sort_keys=True))
        return sha

    def blob_file(self, path: Path, *, note: str = "") -> str:
        return self.blob(Path(path).read_bytes(), note=note or str(path))

    def blob_index(self) -> dict:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text("utf-8"))
        return {}

    def read_blob(self, sha: str) -> bytes:
        target = self.blobs / sha
        if not target.exists():
            raise LedgerError(
                f"blob {sha} is referenced by the ledger and missing from "
                f"{self.blobs}.  The replay cannot reproduce that operation "
                f"and will not guess at a substitute.")
        got = hashlib.sha256(target.read_bytes()).hexdigest()
        if got != sha:
            raise LedgerError(
                f"blob {sha} digests to {got}: the stored payload has been "
                f"modified.  Refusing to replay it.")
        return target.read_bytes()

    # -- append ------------------------------------------------------------

    def append(self, op: str, *, params: dict, wire: list[str] | None = None,
               requires: dict | None = None, expect: dict | None = None,
               meta: dict | None = None) -> dict:
        return self._append_raw(op, params, wire=list(wire or []),
                                requires=dict(requires or {}),
                                expect=expect, meta=dict(meta or {}))

    def _append_raw(self, op: str, params: dict, *, wire: list[str],
                    requires: dict, expect: dict | None, meta: dict) -> dict:
        rec: dict = {
            "seq": self._seq + 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actor": self.actor,
            "op": op,
            "params": params,
            "wire": wire,
            "requires": requires,
            "meta": meta,
            "prev": self._head,
        }
        if expect is not None:
            rec["expect"] = expect

        problems = schema.validate(rec)
        problems += self._cross_record_checks(rec)
        if problems:
            raise LedgerError(
                f"refusing to append {op} (seq {rec['seq']}, actor "
                f"{self.actor}):\n  - " + "\n  - ".join(problems))

        line = schema.canonical(rec).decode("utf-8") + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self._seq = rec["seq"]
        self._head = schema.digest(rec)
        return rec

    # -- the invariants that make a defect unreproducible ------------------

    def _cross_record_checks(self, rec: dict) -> list[str]:
        """Checks that need the whole ledger, not one record.

        Each one corresponds to a defect this project has already paid for, and
        each is enforced here rather than remembered, because "remember not to
        flatten under a pier" is what failed last time.
        """
        bad: list[str] = []
        op, p = rec["op"], rec["params"]
        prior = self.records()

        if op == "terrain_write":
            bad += self._zone_clobber_check(rec, prior)
            bad += self._flatten_forbidden_check(rec, prior)

        if op == "portal":
            tag = p.get("tag")
            ends = [r for r in prior
                    if r["op"] == "portal" and r["params"].get("tag") == tag
                    and r["seq"] not in retired_seqs(prior)]
            if len(ends) >= 2:
                bad.append(
                    f"portal tag {tag!r} already has {len(ends)} ends at seq "
                    f"{[r['seq'] for r in ends]}.  A third portal with an "
                    f"existing tag makes the pair NONDETERMINISTIC on every "
                    f"load: Game::FindRandomUnconnectedPortal pairs on exact "
                    f"string equality and then draws Random.Range(0, count). "
                    f"Two ends per tag, no more.")

        return bad

    def _zone_clobber_check(self, rec: dict, prior: list[dict]) -> list[str]:
        """One zone holds one compiler, so the second write erases the first.

        MEASURED: `Heightmap::GetAndCreateTerrainCompiler` returns the FIRST
        compiler it finds, and the write path issues
        `deleteObjects -zone ZX ZZ -prefab _TerrainCompiler -force` before
        spawning.  A 20 km ribbon crosses roughly 300 zones and will meet pad
        zones, so an unguarded second write is the normal case.
        """
        bad = []
        seen: dict[tuple[int, int], list[int]] = {}
        for r in prior:
            if r["op"] == "terrain_write":
                for e in r["params"]["entries"]:
                    seen.setdefault(tuple(e["zone"]), []).append(r["seq"])
            elif (r["op"] == "observe"
                  and r["params"].get("what") == "zone_already_written"):
                # Writes that happened OUTSIDE the ledger, declared after the
                # fact.  Item 8 of this artefact's limits is that the clobber
                # guard sees only what goes through the ledger, and the
                # honest answer to a pre-rule ad-hoc write is to let the
                # agent that made it DECLARE the zones rather than to fake a
                # terrain_write whose bytes nobody has.  A declared zone arms
                # the guard exactly like a recorded one.
                for z in (r["params"].get("value") or {}).get("zones", []):
                    seen.setdefault((int(z[0]), int(z[1])), []).append(r["seq"])
        for e in rec["params"]["entries"]:
            key = tuple(e["zone"])
            earlier = seen.get(key, [])
            if not earlier:
                continue
            named = set(e.get("merged_from") or [])
            missing = [s for s in earlier if s not in named]
            if missing:
                bad.append(
                    f"terrain_write to zone {list(key)} would DESTROY the "
                    f"compiler written at seq {missing}: a zone holds exactly "
                    f"one _TerrainCompiler and this op deletes before it "
                    f"spawns.  Re-read those blobs, union the samples, and "
                    f"name them in entries[].merged_from with "
                    f"merge_policy='union' -- or state merge_policy='replace' "
                    f"and name them anyway if the overwrite is deliberate.")
        return bad

    def _flatten_forbidden_check(self, rec: dict, prior: list[dict]) -> list[str]:
        """No levelling under an over-water structure.

        This is the `early-dock` defect: the site solved to water_dist_m 0.0,
        the pad levelled the whole rectangle, and afterwards there was no water
        left under the footprint -- so an over-water pier body stood on dry
        levelled ground and read as the floating defect.  Over-water pads are
        the exception to the flatten step, not a variant of it.

        The test is deliberately coarse -- a zone-level overlap against any
        structure that declared `flatten: "FORBIDDEN"` -- because a terrain
        write is zone-granular and a false refusal costs a `merge_policy`
        argument while a false pass costs the operator's trust again.
        """
        bad = []
        forbidden = [r for r in prior
                     if r["params"].get("flatten") == "FORBIDDEN"]
        if not forbidden:
            return bad
        zones = {tuple(e["zone"]) for e in rec["params"]["entries"]}
        for r in forbidden:
            p = r["params"]
            pos = p.get("pos") or [p.get("anchor", {}).get("x"),
                                   None,
                                   p.get("anchor", {}).get("z")]
            if pos[0] is None or pos[2] is None:
                continue
            import math
            zx = math.floor((float(pos[0]) + 32.0) / 64.0)
            zz = math.floor((float(pos[2]) + 32.0) / 64.0)
            if (zx, zz) in zones:
                bad.append(
                    f"terrain_write touches zone {[zx, zz]}, which holds the "
                    f"over-water structure at seq {r['seq']} "
                    f"({p.get('role')}) declared flatten=FORBIDDEN: "
                    f"{p.get('flatten_reason', 'no reason recorded')}. "
                    f"Levelling there removes the water the structure stands "
                    f"over -- the early-dock defect. Move the ribbon, or "
                    f"exclude those samples and say so in meta.")
        return bad

    # -- close -------------------------------------------------------------

    def close_report(self) -> dict:
        """Ledger-level invariants that can only be judged when the build
        stops.  Returns a report; `status` is 'complete' or 'incomplete'.
        Nothing is repaired -- a repair mode is exactly the silent
        substitution this design refuses."""
        recs = self.records()
        problems: list[str] = []

        tags: dict[str, list[int]] = {}
        gone = retired_seqs(recs)
        for r in recs:
            if r["op"] == "portal" and r["seq"] not in gone:
                tags.setdefault(r["params"]["tag"], []).append(r["seq"])
        one_ended = {t: s for t, s in tags.items() if len(s) != 2}
        for tag, seqs in sorted(one_ended.items()):
            problems.append(
                f"portal tag {tag!r} has {len(seqs)} end(s) at seq {seqs}, not "
                f"2.  A one-ended tag pairs at random with the world's "
                f"mod-location portals -- the operator's one-way trip.")

        mutating = [r for r in recs if r["op"] in schema.MUTATING]
        no_expect = [r["seq"] for r in mutating if not r.get("expect")]
        if no_expect:
            problems.append(f"mutating ops with no postcondition: {no_expect}")

        missing_blobs = []
        for r in recs:
            for sha in r.get("requires", {}).get("blobs", []):
                if not (self.blobs / sha).exists():
                    missing_blobs.append((r["seq"], sha))
        if missing_blobs:
            problems.append(f"referenced blobs missing: {missing_blobs}")

        by_op: dict[str, int] = {}
        by_actor: dict[str, int] = {}
        for r in recs:
            by_op[r["op"]] = by_op.get(r["op"], 0) + 1
            by_actor[r["actor"]] = by_actor.get(r["actor"], 0) + 1

        return {
            "status": "complete" if not problems else "incomplete",
            "records": len(recs),
            "head": self._head,
            "by_op": by_op,
            "by_actor": by_actor,
            "portal_tags": {t: s for t, s in sorted(tags.items())},
            "problems": problems,
        }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("op", choices=["spec", "fingerprint", "verify", "report"])
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--root")
    ap.add_argument("--light", action="store_true",
                    help="fingerprint without hashing the plugin DLLs")
    args = ap.parse_args()

    if args.op == "spec":
        print(schema.describe())
        return 0
    if args.op == "fingerprint":
        print(json.dumps(fingerprint(args.world, heavy=not args.light),
                         indent=1)[:4000])
        return 0

    root = Path(args.root) if args.root else RUNS / args.world
    led = Ledger(root, actor="cli")
    if args.op == "verify":
        print(f"chain OK: {led.seq + 1} records, head {led.head}")
        return 0
    report = led.close_report()
    print(json.dumps(report, indent=1))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
