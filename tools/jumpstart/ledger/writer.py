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

import gzip
import math
import hashlib
import json
import os
import platform
import re
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

# The append lock. MEASURED tonight: `_append_raw` read the chain head,
# computed `prev` and wrote, with no lock and with the head CACHED at open, so
# two concurrent appenders forked the chain at file line 47 and every
# subsequent append refused for every agent. The console token could never
# have prevented it -- `note` and `observe` send nothing to the console and
# take no token -- so the guard has to live at the FILE layer.
LOCK_TIMEOUT_S = 30.0

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


# How far the ground may move inside a protected structure's footprint before
# the write is refused.  Not zero: a bilinear blend of unwritten samples is
# exactly 0.0, so anything above float noise means real samples were written
# under the structure.
FLATTEN_TOLERANCE_M = 0.01

# Probe pitch across a protected disc.  The sample lattice is 1 m, so a 0.5 m
# probe cannot step over a written sample.
FLATTEN_PROBE_M = 0.5

# How far a same-`role` record may sit from the protected one and
# still count as the same SITE when neither declares a site_id.
_SITE_RADIUS_M = 64.0


def _record_xz(p: dict) -> tuple | None:
    """A record's world XZ, whatever shape its op uses to carry it.

    THE POSITION FIELD IS NOT ONE SHAPE, and assuming it was made this guard
    CRASH rather than refuse.  MEASURED: `spawn` and `portal` carry `pos` as
    [x, y, z], `zones_generate` carries it as [x, z], `objects_clear` calls it
    `centre`, and `spawn_plan` has no `pos` at all -- it has an `anchor`.
    Every one of those can carry `flatten: "FORBIDDEN"`, and Crossings'
    zones_generate records do, so indexing pos[2] unconditionally raised
    IndexError and took down every terrain_write on this world instead of
    answering the question.
    """
    pos = p.get("pos") or p.get("centre")
    if isinstance(pos, (list, tuple)) and len(pos) == 3:
        return (pos[0], pos[2])
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        return (pos[0], pos[1])
    anchor = p.get("anchor")
    if isinstance(anchor, dict):
        x, z = anchor.get("x"), anchor.get("z")
        if x is not None and z is not None:
            return (x, z)
    return None


def _protected_discs(rec: dict) -> list[tuple]:
    """(x, z, radius) discs a protected structure actually occupies.

    Read from the structure's OWN `expect.prefab_count`, which carries `pos`
    and `max` per probed prefab -- evidence the protecting agent already
    recorded for its own verification, not a new declaration invented for
    this check and not a number supplied by the writer being checked.
    """
    discs = []
    for c in (rec.get("expect") or {}).get("prefab_count", []) or []:
        pos, radius = c.get("pos"), c.get("max")
        if (isinstance(pos, (list, tuple)) and len(pos) == 2
                and isinstance(radius, (int, float)) and radius > 0):
            discs.append((float(pos[0]), float(pos[1]), float(radius)))
    return discs


_SPAWN_POS_RE = re.compile(r"^spawn_object\s+(\S+)\s+pos=([^\s,]+),([^\s,]+),")


def _protected_pieces(rec: dict, ledger: "Ledger") -> list[tuple] | None:
    """Every PIECE position of a protected structure, as (x, z).

    Strictly better evidence than the structure's probe disc, and it is
    already recorded: a `spawn_plan`'s blob IS the literal command list, so
    the pieces can be read back exactly.  MEASURED by RoadBuild against the
    disc test on seven road segments -- the disc gets ALL SEVEN wrong in one
    direction or the other.  Four segments are provably harmless at every one
    of a structure's pieces while their discs overlap, and three genuinely
    disturb a structure (worst applied delta 2.896 m at a boathouse piece).
    A disc is a bounding circle over a probe radius; the pieces are the
    thing that must not move.

    THE FIELD ORDER IS z,x,y AND NOT WHAT YOU WOULD GUESS.  `spawn_object`'s
    `pos=` takes z first (`Parse::VectorZXYRange`), so field0 is Z and field1
    is X.  MEASURED twice independently: `to_rcon_plan.pos_arg`'s own IL-read
    docstring, and RoadBuild cross-checking three structures' plan fields
    against their `expect.prefab_count` (x, z) -- the harbour's expect
    (4.5, -265.2) has field0 in -276.9..-256.5 and field1 in -7.5..12.9.
    Read it the other way round and every piece lands in the wrong zone.

    Returns None when the pieces cannot be read, which the caller must treat
    as "cannot measure" rather than "nothing there".
    """
    p = rec.get("params", {})
    if rec.get("op") in ("spawn", "portal"):
        xz = _record_xz(p)
        return [(float(xz[0]), float(xz[1]))] if xz else None
    if rec.get("op") != "spawn_plan":
        return None
    shas = [p.get("plan_sha256")] + list(
        (rec.get("requires") or {}).get("blobs", []))
    for sha in [s for s in shas if s]:
        try:
            text = ledger.read_blob(sha).decode("utf-8", "replace")
        except LedgerError:
            continue
        pts = []
        for line in text.splitlines():
            m = _SPAWN_POS_RE.match(line.strip())
            if m:
                try:
                    pts.append((float(m.group(3)), float(m.group(2))))
                except ValueError:
                    continue
        if pts:
            return pts
    return None



def _disarmed_by_census(protected: dict, prior: list[dict]) -> bool:
    """Has an actor MEASURED this protected site to hold nothing?

    Accepts `observe what="structure_never_built"` whose `value` names the
    protected record's seq and carries a live per-prefab census that is zero
    throughout.  MEASURED example that motivated it: `dock-stationhub` was
    routed and reserved by Crossings and never built, so its seq 38
    `zones_generate` carries flatten=FORBIDDEN with no extent and fails
    closed over a zone containing nothing -- refusing a validated 578 m road
    segment to protect a structure that does not exist.

    Deliberately strict: a census with no zero counts, or one that does not
    name the seq, does not disarm anything.
    """
    for r in prior:
        if r.get("op") != "observe":
            continue
        p = r.get("params", {})
        if p.get("what") != "structure_never_built":
            continue
        v = p.get("value") or {}
        if int(v.get("protects_seq", -1)) != int(protected.get("seq", -2)):
            continue
        counts = v.get("counts") or {}
        if counts and all(int(n) == 0 for n in counts.values()):
            return True
    return False



def _site_key(p: dict) -> tuple | None:
    """The identity of the SITE a record belongs to, if it declares one."""
    for field in ("site_id", "installation_id"):
        v = p.get(field)
        if isinstance(v, str) and v:
            return (field, v)
    return None


def _site_evidence(protected: dict, prior: list[dict],
                   ledger: "Ledger") -> tuple[list, list]:
    """Pieces and discs from EVERY record belonging to the protected SITE.

    A STRUCTURE IS A SITE, AND THE RECORD CARRYING THE FLAG IS OFTEN NOT THE
    RECORD CARRYING THE PIECES.  MEASURED by RoadBuild: the spawn portal
    ring's only `flatten: FORBIDDEN` record is a `zones_generate` with no
    pieces and no disc, while the four portals it exists to protect are
    separate `portal` records whose own `flatten` is unset.  Grouped by
    record there is nothing to test and the guard falls through to a
    fail-closed zone refusal; grouped by site there are four exact
    positions.  The same shape blocked four more segments, because Crossings
    repeats the flag on each site's `zones_generate` and `objects_clear`
    siblings while the pieces live in that site's `spawn_plan` blob.

    Nothing is loosened: a site with no pieces anywhere still refuses.

    Grouping is by `site_id`/`installation_id` when declared.  Falling back
    to bare `role` would merge every dock in the world into one site and
    refuse a road near one because another is 4 km away, so the role
    fallback is additionally bounded to records within `_SITE_RADIUS_M` of
    the protected record's own position.
    """
    pieces: list = []
    discs: list = []
    key = _site_key(protected.get("params", {}))
    # An EXPLICIT association, keyed by FILE LINE, for members a site cannot
    # be inferred to own.  MEASURED on the live ledger: the spawn portal
    # ring's `zones_generate` carries site_id "portal-ring" and finds ZERO
    # pieces, because the four portals standing in the ring are the hub ENDS
    # of pairs belonging to other sites -- they carry site_id "stenvik",
    # "harbour-temple-south" and "ferry-terminal-eastisle".  No rule over
    # site_id, role or distance can group those correctly, so the link is
    # declared and measured rather than guessed, and it is keyed by file
    # line because after the fork `seq` is not unique.
    associated = set()
    if key is not None:
        for r in prior:
            if r.get("op") != "observe":
                continue
            rp = r.get("params", {})
            if rp.get("what") != "site_association":
                continue
            v = rp.get("value") or {}
            if v.get("site_id") != key[1]:
                continue
            associated.update(int(n) for n in (v.get("lines") or []))
    role = protected.get("params", {}).get("role")
    here = _record_xz(protected.get("params", {}))
    # A RETIRED PIECE IS NOT STANDING THERE, and protecting it asserts
    # something true about the log and false about the world -- which is the
    # substitution `retire` exists to stop (see its `why`: "so that a REPLAY
    # ENDS WITH THE OBJECTS GONE rather than faithfully rebuilding litter").
    # MEASURED case that found this: the portal hall's pad probes 1.500 m of
    # applied delta at (-281.5, 197.5), which is the hub-END arch of tag
    # `x-ferry-e`. That arch carries `site_id: ferry-terminal-eastisle`
    # because its PAIR stands on a jetty 2.3 km away, so the jetty's
    # `flatten: FORBIDDEN` -- "levelling the footprint removes the water the
    # piles are driven into" -- reaches across the world and refuses a pad
    # under an arch that stands in a wood. Deleting that arch and re-seating
    # it inside the hall is exactly the fix, and once it is retired there is
    # nothing at that position to protect. Nothing is loosened: an
    # unretired piece is still protected, and a retire is itself a recorded,
    # verified operation whose postcondition counts the objects to zero.
    gone = retired_seqs(prior)

    for line_no, r in enumerate(prior + [protected]):
        rp = r.get("params", {})
        if r.get("seq") in gone and r is not protected:
            continue
        same = line_no in associated
        if not same and key is not None and _site_key(rp) == key:
            same = True
        elif key is None and role and rp.get("role") == role:
            there = _record_xz(rp)
            if here and there:
                same = (math.hypot(float(there[0]) - float(here[0]),
                                   float(there[1]) - float(here[1]))
                        <= _SITE_RADIUS_M)
        if not same:
            continue
        got = _protected_pieces(r, ledger)
        if got:
            pieces.extend(got)
        discs.extend(_protected_discs(r))
    return pieces, discs


class _DeltaField:
    """The height change one `terrain_write` applies, at any world position.

    Decoded from the write's OWN blobs, so the guard measures the bytes that
    are about to land rather than a summary of them.

    Bilinear, because `Heightmap` renders a mesh that interpolates LINEARLY
    between adjacent samples at a 1 m pitch: the ground under an off-lattice
    point moves by the blend of the four samples around it, and an UNWRITTEN
    sample contributes zero.  Same primitive as the road rasteriser's
    `delta_at`; reimplemented here rather than imported so that `writer.py`
    does not depend on another agent's module.
    """

    def __init__(self, ledger: "Ledger", rec: dict):
        self.zones: dict[tuple, tuple] = {}
        # Zones whose bytes could not be read or decoded.  FAIL CLOSED on
        # these: an undecodable blob means the guard does not know what the
        # write does there, and treating "I could not measure it" as "it is
        # fine" is the substitution this whole design refuses.  MEASURED by
        # the guard suite: without this, a terrain_write referencing a blob
        # that is not in the store passed a FORBIDDEN footprint it overlapped,
        # because an unreadable zone contributes a delta of zero.
        self.unreadable: set[tuple] = set()
        for e in rec["params"]["entries"]:
            key = (int(e["zone"][0]), int(e["zone"][1]))
            try:
                blob = ledger.read_blob(e["blob_sha256"])
            except LedgerError:
                self.unreadable.add(key)
                continue
            decoded = _decode_tcdata(blob)
            if decoded is None:
                self.unreadable.add(key)
                continue
            self.zones[key] = (tuple(e["centre"]), decoded)

    def delta_at(self, x: float, z: float) -> float:
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        total = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            if w == 0.0:
                continue
            sx, sz = x0 + dx, z0 + dz
            key = (math.floor((sx + 32.0) / 64.0), math.floor((sz + 32.0) / 64.0))
            entry = self.zones.get(key)
            if entry is None:
                continue
            (cx, cz), (modified, level) = entry
            gx = math.floor((sx - cx) / 1.0 + 0.5) + 32
            gy = math.floor((sz - cz) / 1.0 + 0.5) + 32
            if not (0 <= gx < 65 and 0 <= gy < 65):
                continue
            k = gy * 65 + gx
            if modified[k]:
                total += w * level[k]
        return total

    def worst_delta_over_disc(self, x: float, z: float,
                              radius: float) -> tuple:
        worst, at = 0.0, (x, z)
        steps = max(1, int((2 * radius) / FLATTEN_PROBE_M))
        for i in range(steps + 1):
            px = x - radius + i * FLATTEN_PROBE_M
            for j in range(steps + 1):
                pz = z - radius + j * FLATTEN_PROBE_M
                if (px - x) ** 2 + (pz - z) ** 2 > radius * radius:
                    continue
                d = abs(self.delta_at(px, pz))
                if d > worst:
                    worst, at = d, (px, pz)
        return worst, at


def _decode_tcdata(blob: bytes):
    """(modified_height[], level_delta[]) out of a gzip'd TCData payload.

    The inverse of `tcdata.Compiler.plain`: int32 version, int32 operations,
    4 floats of op record, int32 count then a flag+2-float record per sample.
    Only the height half is read; paint cannot move the ground.
    """
    try:
        plain = gzip.decompress(blob)
    except OSError:
        return None
    off = 0
    _version, _ops = struct.unpack_from("<ii", plain, off)
    off += 8
    off += 16  # last_op_point (3f) + last_op_radius (f)
    (count,) = struct.unpack_from("<i", plain, off)
    off += 4
    modified = bytearray(count)
    level = [0.0] * count
    for i in range(count):
        flag = plain[off]
        off += 1
        if flag:
            level[i], _smooth = struct.unpack_from("<ff", plain, off)
            off += 8
            modified[i] = 1
    return modified, level

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

    def build(self, op: str, *, params: dict, wire: list[str] | None = None,
              requires: dict | None = None, expect: dict | None = None,
              meta: dict | None = None) -> dict:
        """The record a call to `append` WOULD write, without writing it.

        This is what a dry run needs and what it did not have: the useful half
        of a dry run is `schema.validate`, and until now the only way to reach
        it was to append.  So "test the gate without touching the shared
        artefact" was not expressible, and two agents in one session left
        records in the chain while trying not to.

        `seq` and `prev` are the CURRENT head's successor rather than a
        placeholder, so the validated shape is the one that would land -- but
        nothing is locked, written or fsync'd, and the cross-record checks
        (which read prior records and must run inside the append lock) are
        deliberately NOT run here: a dry run cannot hold a claim on the chain.
        """
        seq, head = self._replay_chain()
        rec: dict = {
            "seq": seq + 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actor": self.actor,
            "op": op,
            "params": params,
            "wire": list(wire or []),
            "requires": dict(requires or {}),
            "meta": dict(meta or {}),
            "prev": head,
        }
        if expect is not None:
            rec["expect"] = expect
        return rec

    def _append_raw(self, op: str, params: dict, *, wire: list[str],
                    requires: dict, expect: dict | None, meta: dict) -> dict:
        """Append one record under an EXCLUSIVE FILE LOCK, with the chain head
        RE-READ inside the critical section.

        BOTH halves are load bearing and MEASURED. The lock alone would not
        have prevented tonight's fork: `__init__` caches `_seq`/`_head`, and
        the builder that forked the chain had been open for four minutes, so
        it would have taken the lock and then written against a head that went
        stale while it held nothing. So the head is read from the FILE at every
        append, and the next seq comes from the LAST LINE rather than from a
        count -- after a fork the numbers are duplicated and a count hands out
        a seq that already exists twice.

        The lock covers `_cross_record_checks` too, deliberately: the portal
        two-ends-per-tag guard and the one-compiler-per-zone clobber guard
        DECIDE BY READING PRIOR RECORDS, so a concurrent append between the
        read and the write is exactly the window in which both go silently
        inert. It does NOT cover the console round trip -- `emit` sends after
        `append` returns -- because one agent's RCON latency must not
        serialise everybody's logging.
        """
        import fcntl

        lock_path = self.root / "ledger.lock"
        deadline = time.time() + LOCK_TIMEOUT_S
        with lock_path.open("a+", encoding="utf-8") as lock:
            while True:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() >= deadline:
                        lock.seek(0)
                        raise LedgerError(
                            f"ledger append lock held by {lock.read()[:200]!r} "
                            f"and not released within {LOCK_TIMEOUT_S:g}s. A "
                            f"crashed holder reads as this message rather than "
                            f"as a hang; clear {lock_path} only after checking "
                            f"that pid is gone.")
                    time.sleep(0.05)
            lock.seek(0)
            lock.truncate()
            lock.write(f"pid {os.getpid()} actor {self.actor} op {op} "
                       f"since {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
            lock.flush()
            try:
                self._seq, self._head = self._replay_chain()
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
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

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

        FOOTPRINT-GRANULAR, not zone-granular.  The coarse version was right
        when the only writers were pads -- a false refusal cost one argument.
        A 20 km ribbon inverts that: it crosses hundreds of zones and would be
        refused for passing 60 m from a bridge in the same 64 m cell, and a
        guard that is refused-around stops being a guard.

        The real verdict is computed from evidence BOTH sides already record,
        never declared by the writer being checked: the protected structure's
        own disc comes from its `expect.prefab_count` (pos + max radius), and
        the applied height change comes from THIS write's own TCData blobs,
        bilinearly blended exactly as `Heightmap` renders them.  Zone overlap
        is only the trigger.

        FAIL CLOSED.  A protected structure whose extent cannot be read falls
        back to refusing the whole zone: "I could not measure it" is not "it
        is fine", and that substitution is this project's signature defect.
        """
        import math

        bad = []
        retired = retired_seqs(prior)
        forbidden = [r for r in prior
                     if r["params"].get("flatten") == "FORBIDDEN"
                     and r["seq"] not in retired]
        if not forbidden:
            return bad
        zones = {tuple(e["zone"]) for e in rec["params"]["entries"]}
        field = None  # decoded lazily: only needed once a trigger fires

        for r in forbidden:
            p = r["params"]
            # PIECES FIRST: the disc is a bounding circle over a probe
            # radius, the pieces are what must not move, and both are already
            # recorded.  Fall back to the disc only when the pieces cannot be
            # read.
            pieces, site_discs = _site_evidence(r, prior, self)
            discs = ([(px, pz, 0.0) for px, pz in pieces] if pieces
                     else site_discs)
            if not discs and not pieces and _disarmed_by_census(r, prior):
                # DISARMED BY MEASUREMENT, and only in the one case where the
                # guard would otherwise refuse for absence of evidence: a
                # record with no readable pieces AND no disc, whose site an
                # actor has since MEASURED to be empty.  The mirror of
                # `zone_already_written`, which arms the clobber guard from a
                # declaration; this disarms from a census.  It cannot loosen
                # the precise test, because a structure with readable pieces
                # never reaches here -- and if the site is built later, that
                # build is a new record carrying its own pieces and its own
                # protection.  The default stays fail-closed: absence of
                # evidence still refuses; only evidence of absence moves it.
                continue
            if not discs:
                # No readable extent -> zone-granular refusal, as before.
                xz = _record_xz(p)
                if xz is None:
                    continue
                zx = math.floor((float(xz[0]) + 32.0) / 64.0)
                zz = math.floor((float(xz[1]) + 32.0) / 64.0)
                if (zx, zz) in zones:
                    bad.append(
                        f"terrain_write touches zone {[zx, zz]}, which holds "
                        f"the over-water structure at seq {r['seq']} "
                        f"({p.get('role')}) declared flatten=FORBIDDEN: "
                        f"{p.get('flatten_reason', 'no reason recorded')}. "
                        f"That record carries NO measurable extent (no "
                        f"`expect.prefab_count` with pos+max), so the precise "
                        f"test cannot run and the whole zone is refused. Add "
                        f"the structure's disc to its record to get a "
                        f"footprint-granular answer.")
                continue

            for disc in discs:
                dx, dz, radius = disc
                zx = math.floor((float(dx) + 32.0) / 64.0)
                zz = math.floor((float(dz) + 32.0) / 64.0)
                # Trigger on any zone the disc can reach, not just its centre's.
                reach = {(zx + i, zz + j)
                         for i in range(-1, 2) for j in range(-1, 2)}
                if not (reach & zones):
                    continue
                if field is None:
                    field = _DeltaField(self, rec)
                blind = field.unreadable & reach
                if blind:
                    bad.append(
                        f"terrain_write cannot be checked against the "
                        f"over-water structure at seq {r['seq']} "
                        f"({p.get('role')}): the TCData for zone(s) "
                        f"{sorted(blind)} could not be read or decoded, so "
                        f"the applied height change inside its "
                        f"{radius:.1f} m footprint is UNKNOWN. Refusing "
                        f"rather than assuming zero -- an unreadable blob is "
                        f"not a flat one.")
                    continue
                if radius <= 0.0:
                    # A piece: probe it exactly. `delta_at` is the bilinear
                    # blend Heightmap renders, so an untouched piece reads
                    # exactly 0.0 and there is nothing to sweep.
                    worst, at = abs(field.delta_at(dx, dz)), (dx, dz)
                else:
                    worst, at = field.worst_delta_over_disc(dx, dz, radius)
                if worst > FLATTEN_TOLERANCE_M:
                    bad.append(
                        f"terrain_write would move the ground by {worst:.3f} m "
                        f"at ({at[0]:.1f}, {at[1]:.1f}), "
                        + (f"AT A PIECE of the over-water structure "
                           if radius <= 0.0 else
                           f"inside the {radius:.1f} m footprint of the "
                           f"over-water structure ") +
                        f"at seq {r['seq']} ({p.get('role')}) declared "
                        f"flatten=FORBIDDEN: "
                        f"{p.get('flatten_reason', 'no reason recorded')}. "
                        f"Levelling there removes the water the structure "
                        f"stands over -- the early-dock defect. Move the "
                        f"route, or stop writing samples inside that disc; "
                        f"there is no flag that waives this.")
        return bad

    # -- close -------------------------------------------------------------

    def close_report(self) -> dict:
        """Ledger-level invariants that can only be judged when the build
        stops.  Returns a report; `status` is 'complete' or 'incomplete'.
        Nothing is repaired -- a repair mode is exactly the silent
        substitution this design refuses."""
        recs = self.records()
        problems: list[str] = []

        # Tags are keyed by FILE LINE as well as by seq: after a fork the seq
        # numbers are duplicated, and an invariant that reports "seq 43" when
        # two records carry that label is naming an ambiguous thing. File
        # position is the only total order this artefact has.
        tags: dict[str, list[int]] = {}
        tag_lines: dict[str, list[int]] = {}
        gone = retired_seqs(recs)
        for line, r in enumerate(recs):
            if r["op"] == "portal" and r["seq"] not in gone:
                tags.setdefault(r["params"]["tag"], []).append(r["seq"])
                tag_lines.setdefault(r["params"]["tag"], []).append(line)
        one_ended = {t: s for t, s in tags.items() if len(s) != 2}
        for tag, seqs in sorted(one_ended.items()):
            problems.append(
                f"portal tag {tag!r} has {len(seqs)} end(s) at seq {seqs} "
                f"(file line(s) {tag_lines[tag]}), not 2.  A one-ended tag "
                f"pairs at random with the world's mod-location portals -- "
                f"the operator's one-way trip.")

        # THE FORK IS REPORTED HERE OR IT IS INVISIBLE. `Ledger.open` now
        # tolerates a labelled fork so that a damaged log can still record
        # that it is damaged -- which means the close report is the thing that
        # has to say so, every time, rather than the open path failing loudly
        # once. MEASURED consequence of the fork it was written for: for the
        # window it was open, the two guards above were INERT, because both
        # decide by READING PRIOR RECORDS and a reader that stops at the first
        # branch cannot see claims made on the second.
        integrity = schema.scan(
            self.path.read_text("utf-8").splitlines()) if self.path.exists() \
            else {"forks": [], "duplicate_seqs": {}}
        for fork in integrity["forks"]:
            problems.append(
                f"chain FORK at file line {fork['line']} (seq {fork['seq']}, "
                f"{fork['actor']} {fork['op']} at {fork['ts']}): declared "
                f"prev={fork['declared_prev'][:12]} but the preceding line "
                f"digests to {fork['actual_prev'][:12]}. Two appenders raced. "
                f"Replay MUST order by file position; `seq` is advisory from "
                f"this line on, and every guard that reads prior records was "
                f"blind to one branch until this was labelled.")
        if integrity["duplicate_seqs"]:
            problems.append(
                f"duplicate seq labels: "
                f"{ {s: ls for s, ls in integrity['duplicate_seqs'].items()} } "
                f"(seq -> file lines). Key resume progress and `merged_from` "
                f"by FILE LINE, not by seq.")

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
        # `led.seq` is the LAST LINE's seq LABEL, and after a fork that label
        # is neither unique nor a count -- MEASURED on the live ledger, it
        # said "227 records" for a file holding 231, because four seqs appear
        # twice.  Report the count the file actually has, and the label
        # separately so the difference is visible rather than reconciled
        # silently.
        recs = led.records()
        print(f"chain OK: {len(recs)} lines, last seq label {led.seq}, "
              f"head {led.head}")
        return 0
    report = led.close_report()
    print(json.dumps(report, indent=1))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
