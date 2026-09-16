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
            (cx, cz), (modified, level, _smooth) = entry
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
    """(modified_height[], level_delta[], smooth_delta[]) out of a gzip'd
    TCData payload.

    The inverse of `tcdata.Compiler.plain`: int32 version, int32 operations,
    4 floats of op record, int32 count then a flag+2-float record per sample.
    Only the height half is read; paint cannot move the ground.

    BOTH height deltas, not just the level.  `TerrainComp::ApplyToHeightmap`
    adds `m_levelDelta + m_smoothDelta`, so a write that moves the ground by
    changing only the smooth half moves it exactly as much as one that
    changes the level half.  The flatten guard reads `level` alone because it
    measures the same field the road rasteriser writes; the pad-footprint
    guard compares two claims' bytes for EQUALITY, and a comparison that
    ignores half the sum reports "these agree" about ground that moved.
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
    smooth = [0.0] * count
    for i in range(count):
        flag = plain[off]
        off += 1
        if flag:
            level[i], smooth[i] = struct.unpack_from("<ff", plain, off)
            off += 8
            modified[i] = 1
    return modified, level, smooth

# A PAD IS RECOGNISED BY ITS SHAPE, NOT BY A ROLE ALLOW-LIST.  `terrain_write`
# already enforces `target_y` XOR `profile` -- "a pad has a single target
# height, a road has a longitudinal profile, and neither shape can express the
# other" -- and a pad additionally carries its own rectangle in `pad_w`/`pad_d`
# plus `apron_m`.  So the schema's own distinction answers "is this claim a
# foundation", and a new settlement role cannot fall out of the guard's scope
# by being spelled differently.  MEASURED on this ledger: 50 of 79
# `terrain_write` records carry `pad_w`, and they are exactly the 49 `site_pad`
# records plus the `portal_hall_pad`.
def _is_pad(p: dict) -> bool:
    return (p.get("pad_w") is not None or p.get("pad_d") is not None) \
        and p.get("target_y") is not None


def _pad_footprint(rec: dict, prior_through: list[dict]) -> dict | None:
    """A pad claim's rectangle in world XZ, from records it already carries.

    `pad_w`/`pad_d` are the FULL width and depth (`settlements/build.py`:
    `hw, hd = unit["pad_w"] / 2 + APRON_M, ...`), so the half-extents are
    `pad_w / 2 + apron_m`.  Reading them as half-extents would double every
    footprint in the world and refuse roads 18 m outside a pad they never
    touch.

    THE POSITION IS NOT IN THE `terrain_write` RECORD, and this is the one
    place this guard has to reach outside the record it is judging: a pad's
    `terrain_write` carries `pad_w`, `pad_d`, `apron_m`, `target_y` and
    `site_id`, and no `pos` at all -- its `entries[].centre` are ZONE
    centres, four of them, none of which is the pad.  The position comes from
    the same site's sibling records, which do carry one: `objects_clear`'s
    `centre`, the body `spawn_plan`'s `anchor`/`pos`.  MEASURED on this
    ledger: all 50 pad writes resolve a position this way, 0 do not.
    """
    p = rec.get("params", {})
    if not _is_pad(p):
        return None
    sid = p.get("site_id")
    if not sid:
        return None
    pos = None
    for r in prior_through:
        rp = r.get("params", {})
        if rp.get("site_id") != sid:
            continue
        xz = _record_xz(rp)
        if xz:
            pos = (float(xz[0]), float(xz[1]))
            break
    if pos is None:
        return None
    w = float(p.get("pad_w") or p.get("pad_d"))
    d = float(p.get("pad_d") or p.get("pad_w"))
    ap = float(p.get("apron_m") or 0.0)
    return {"seq": rec["seq"], "name": p.get("name"), "site_id": sid,
            "role": p.get("role"), "x": pos[0], "z": pos[1],
            "hw": w / 2.0 + ap, "hd": d / 2.0 + ap}


def _pad_claims(prior: list[dict]) -> list[dict]:
    """Every live pad footprint in the chain, ONE PER SITE, carrying that
    site's LATEST claim, in file order.

    ONE PER SITE, because a site's pad is re-emitted: `stenvik-hall-1` has
    three `terrain_write` records and `tree-sth-1` two, all with the same
    rectangle.  Reported per record the same contested sample is refused
    three times with three seq labels, which reads as three defects; and the
    floor to compare against is the LAST one, by the same file-order rule the
    live compiler follows.
    """
    gone = retired_seqs(prior)
    by_site: dict[str, dict] = {}
    for line, r in enumerate(prior):
        if r.get("op") != "terrain_write" or r.get("seq") in gone:
            continue
        foot = _pad_footprint(r, prior[:line + 1])
        if foot:
            foot["file_line"] = line
            by_site[foot["site_id"]] = foot
    return sorted(by_site.values(), key=lambda f: f["file_line"])


def _entry_samples(e: dict, ledger: "Ledger"):
    """`{(sx, sz): (level, smooth)}` for one `terrain_write` entry, keyed by
    WORLD SAMPLE rather than by lattice index.

    Keyed by world sample because the 65 x 65 lattice over a 64 m zone SHARES
    ITS BOUNDARY ROW: one world sample sits in two adjacent zones' compilers,
    so comparing claim A's index k against claim B's index k is comparing two
    different square metres whenever their zones differ.  MEASURED by
    `ribbon.py`'s rule-2 guard: sample (498, 96) is `wt-south` seq 373's pad
    in zone (8,1) while `zone_of` puts it in (8,2), and (522, 480) is held as
    a pad by zone (8,7) and as a road by zone (8,8).  Index arithmetic cannot
    see either.

    Returns None when the blob cannot be read or decoded -- "I could not
    measure it" is not "it is flat".
    """
    try:
        blob = ledger.read_blob(e["blob_sha256"])
    except LedgerError:
        return None
    decoded = _decode_tcdata(blob)
    if decoded is None:
        return None
    modified, level, smooth = decoded
    cx, cz = float(e["centre"][0]), float(e["centre"][1])
    out = {}
    for i, flag in enumerate(modified):
        if not flag:
            continue
        gy, gx = divmod(i, 65)
        out[(int(cx + gx - 32), int(cz + gy - 32))] = (level[i], smooth[i])
    return out

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
            bad += self._pad_footprint_check(rec, prior)

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

    def _pad_footprint_check(self, rec: dict, prior: list[dict]) -> list[str]:
        """NO AUTHORED SAMPLE INSIDE ANOTHER CLAIM'S PAD FOOTPRINT.

        Inside a settlement pad the pad wins: it is a foundation, and a
        building whose floor has been regraded is a defect that reads as the
        floating defect.  The road owns everything outside the pad and MUST
        grade into the pad's EDGE -- that approach is required, it is what
        took T12's junction step from 2.991 m to 0.070 m over 11 nodes -- so
        the test is not "does this write come near a pad" but "does it AUTHOR
        a sample inside one".

        WHY THIS IS A WRITE-TIME GUARD AND NOT A TOOL-TIME ONE.  `ribbon.py`
        already refuses this per sample before it sends (its rule 2), and
        that is the right place to catch it early -- but it is one tool.  The
        two invariants this file already enforces exist because "an ad-hoc
        `spawn_object` or a hand-run `objects_remove` is invisible to them";
        the same is true here.  Any `terrain_write` that reaches the chain
        passes through `append`, so this is the only place the rule holds for
        a writer that is not `ribbon.py`, for a `ribbon.py` whose rewind is
        removed, and for a pad that lands between a plan and its write.

        AUTHORED IS MEASURED AS DISAGREEMENT, WHICH IS THE ONLY HONEST
        ANSWER THE CURRENT SCHEMA CAN GIVE.  The blob a claim sends is the
        UNION of every delta its zones must keep -- a zone holds exactly one
        `_TerrainCompiler` and this op does `deleteObjects -zone` first, so
        anything the blob omits is destroyed.  The record then stores that
        same union, so a pad's floor inside a road's blob reads as
        `role: road_segment` under the road's name: the union LAUNDERS
        ownership.  `merged_from` names the priors but not which samples came
        from them, so the authored set is derivable only where this entry and
        its priors DISAGREE (seq 1638 writes this up as the migration that
        fixes it durably, and it is deliberately not applied yet).

        That limit is exactly the right shape for this question.  Where the
        two agree, the ground does not move, and there is nothing to refuse
        whoever authored it.  Where they disagree inside a pad's rectangle,
        the pad's floor is about to be regraded -- which is the defect --
        regardless of which claim's name is on the blob.  So the guard is
        blind to `role` on the deltas and reads only the bytes.

        THE REWIND IS PART OF THE TEST, AND IT IS AUTHORSHIP RATHER THAN A
        NAME FILTER.  The prior state is composed in FILE ORDER (`seq` is not
        a total order past file line 47), later claim winning per world
        sample, and each sample is attributed to the last claim whose value
        DIFFERED from what the claims before it had composed.  The pad's own
        floor is then the pad's wherever the pad laid it, however many claims
        have carried it forward since.

        EXCLUDING THIS CLAIM'S OWN EARLIER RECORDS BY NAME WAS THE FIRST
        ATTEMPT AND IT IS WRONG IN THE OTHER DIRECTION, measured twice.  It
        cures the T8 case -- 123 carriageway samples its first write refused
        as pad-owned are carried in its own blob, and the identical
        rasterisation against the surface that write produced finds ZERO
        foreign owners -- and it CREATES the S1 case: the portal hall's pad
        seq 801 unioned S1's carriageway forward verbatim ((-300, 214) is
        -1.694 in both blobs, to the bit), so with S1's own records excluded
        the hall becomes the first claim to hold those samples and reads as
        their author.  The guard then refused 68 samples of S1's own ramp to
        the hall, at positions like (-302, 212) that are outside the hall's
        rectangle entirely.  Authorship over the FULL composition answers
        both: nothing is dropped, so a carried copy can never become a first
        write.

        PAD-VERSUS-PAD IS OUT OF SCOPE, deliberately.  Settlement pads
        overlap each other by design -- stenvik alone lays 18 of them at
        26.1 m across a 100 m district, one producer, one ordering, later pad
        winning per sample by the same file-order rule the live compiler
        uses.  Refusing that would refuse `settlements/build.py` its own
        layout.  What must never author a foundation is a claim that is not
        one, so the guard fires on writes that are not themselves pads.

        FAIL CLOSED.  An entry whose blob cannot be read or decoded, in a
        zone a pad footprint reaches, is refused rather than assumed flat.
        """
        p = rec["params"]
        if _is_pad(p):
            return []
        pads = [q for q in _pad_claims(prior)
                if q["site_id"] != p.get("site_id")
                and q["name"] != p.get("name")]
        if not pads:
            return []

        # TRIGGER ON ZONE OVERLAP, VERDICT ON THE RECTANGLE.  Decoding a
        # 20 km ribbon's priors costs real time, and a pad four kilometres
        # away cannot be reached by any sample in these zones.  Neighbouring
        # zones count: a pad's rectangle straddles zone boundaries and the
        # lattice's boundary row is shared.
        zones = {(int(e["zone"][0]), int(e["zone"][1]))
                 for e in rec["params"]["entries"]}
        near = []
        for q in pads:
            zx, zz = math.floor((q["x"] + 32.0) / 64.0), \
                math.floor((q["z"] + 32.0) / 64.0)
            span_x = int(q["hw"] // 64) + 2
            span_z = int(q["hd"] // 64) + 2
            reach = {(zx + i, zz + j)
                     for i in range(-span_x, span_x + 1)
                     for j in range(-span_z, span_z + 1)}
            if reach & zones:
                near.append(q)
        if not near:
            return []

        bad: list[str] = []
        # This write's own samples, per entry, keyed by world sample.
        ours: dict[tuple, tuple] = {}
        for e in rec["params"]["entries"]:
            got = _entry_samples(e, self)
            if got is None:
                bad.append(
                    f"terrain_write cannot be checked against "
                    f"{len(near)} pad footprint(s) it reaches: its own TCData "
                    f"for zone {list(e['zone'])} "
                    f"(blob {str(e.get('blob_sha256'))[:12]}) could not be "
                    f"read or decoded, so which samples it AUTHORS inside "
                    f"those footprints is UNKNOWN. Refusing rather than "
                    f"assuming it writes nothing there -- an unreadable blob "
                    f"is not an empty one.")
                continue
            ours.update(got)
        if bad:
            return bad
        if not ours:
            return []

        # THE PRIOR STATE, REWOUND, AND WHO AUTHORED EACH SAMPLE OF IT.
        # Only at the samples this write holds: composing whole zones costs
        # 4,225 samples per prior blob for an answer that is only ever asked
        # about samples this write touches.
        #
        # ONE PASS IN FILE ORDER, and it has to be a pass rather than a
        # lookup because AUTHORSHIP IS A PROPERTY OF THE SEQUENCE.  Every
        # claim's blob is the union of its zones, so "this claim's blob holds
        # sample k" says nothing about whether it PUT it there -- the pad
        # `stenvik-hut-2` seq 304 is 10.4 x 10.4 m at (504.5, 896.1) and its
        # blob holds (544, 927), forty metres away, because that sample is in
        # one of its zones and it carried it.  A claim AUTHORED k when its
        # value at k differs from the state the claims before it had composed;
        # that is the same disagreement test this whole guard rests on,
        # applied to the priors instead of to the candidate.
        prior_state: dict[tuple, tuple] = {}
        prior_owner: dict[tuple, dict] = {}
        # AND WHAT EACH PAD ITSELF LAID, per pad -- BUT ONLY WHERE THAT PAD IS
        # STILL THE EFFECTIVE AUTHOR OF THE SAMPLE.
        #
        # The first form kept every pad's authored value forever, on the
        # reasoning that "a third claim that paved the foundation in between
        # does not become the new reference just by being later".  That is
        # right about a ROAD paving a foundation and wrong about a later PAD
        # relaying the same ground, and the difference is not academic: it
        # made the check UNSATISFIABLE.
        #
        # MEASURED on T4, samples (480, 908), (481, 908), (482, 908):
        # `stenvik-cottage-2` seq 265 authored -0.3026 / -0.0843 / -0.1069,
        # and `stenvik-stonehouse-2` seq 279 then authored +1.0974 / +1.3157 /
        # +1.2931 -- bit for bit `stenvik-hall-1` seq 61's values, i.e. it
        # composed from a base that predated cottage-2 and REVERTED it.  Both
        # are pads, both "authored" the same square metre, and their values
        # differ by 1.4 m.  A road carrying either one is refused by the
        # other, so T4 could not be written at all -- and the guard's own
        # docstring says PAD-VERSUS-PAD IS OUT OF SCOPE, deliberately,
        # because settlement pads overlap by design and later pad wins per
        # sample by the same file-order rule the live compiler uses.
        #
        # So authorship is resolved to the EFFECTIVE author: the LAST claim to
        # change a sample.  A superseded pad's floor is ground that NO LONGER
        # EXISTS, and judging a road against ground that is not there is the
        # same defect as every other one in this file -- a check answering a
        # question about a world that has moved on.  What the rule still
        # catches, unchanged, is the case it was written for: where a ROAD
        # paved a pad's floor and is the effective author, the pad is not, so
        # the reference falls back to `prior_state` -- the ground that IS
        # there -- and a write that moves it is still refused.
        pad_authored: dict[int, dict] = {}
        authored_at: dict[tuple, int] = {}
        pad_lines = {q["file_line"]: q for q in pads}
        for line, r in enumerate(prior):
            if r.get("op") != "terrain_write":
                continue
            rp = r["params"]
            # NOTHING IS EXCLUDED.  The rewind is authorship, not a name
            # filter: dropping this claim's own records makes a later claim
            # that merely CARRIED its samples read as their author, which
            # refused 68 samples of S1's own ramp to a pad whose rectangle
            # they are outside.  See the docstring.
            if not ({(int(e["zone"][0]), int(e["zone"][1]))
                     for e in rp["entries"]} & {
                        (zx + i, zz + j) for zx, zz in zones
                        for i in (-1, 0, 1) for j in (-1, 0, 1)}):
                continue
            for e in rp["entries"]:
                got = _entry_samples(e, self)
                if got is None:
                    bad.append(
                        f"terrain_write cannot be checked against the pad "
                        f"footprints it reaches: the PRIOR state at seq "
                        f"{r['seq']} ({rp.get('name')}, zone "
                        f"{list(e['zone'])}) could not be read or decoded, so "
                        f"which of this write's samples are AUTHORED rather "
                        f"than carried forward is UNKNOWN. Refusing rather "
                        f"than treating every sample as new.")
                    continue
                for k, v in got.items():
                    if k not in ours:
                        continue
                    was = prior_state.get(k)
                    if was is None or abs((v[0] + v[1]) - (was[0] + was[1])) \
                            > FLATTEN_TOLERANCE_M:
                        prior_owner[k] = {"seq": r["seq"],
                                          "name": rp.get("name"),
                                          "role": rp.get("role"),
                                          "file_line": line}
                        # THE PREVIOUS AUTHOR IS SUPERSEDED.  Dropping the
                        # sample from whichever pad held it is what makes
                        # "the effective author" true rather than merely
                        # intended -- without it the old set survives and the
                        # unsatisfiable pair comes back.
                        prev = authored_at.get(k)
                        if prev is not None and prev != line \
                                and prev in pad_authored:
                            pad_authored[prev].pop(k, None)
                        authored_at[k] = line
                        if line in pad_lines:
                            pad_authored.setdefault(line, {})[k] = v
                    prior_state[k] = v
        if bad:
            return bad

        # A PAD'S FOOTPRINT IS ITS RECTANGLE **OR** THE GROUND IT ITSELF
        # LEVELLED, WHICHEVER IS WIDER, and the second half is not redundant.
        # MEASURED on this ledger, per pad, over each pad's own AUTHORED
        # samples: `stenvik-stonehouse-1` seq 272 authored 165 and only 60 of
        # them are inside its 11.0 x 15.0 m rectangle; `wt-town` seq 387
        # authored 17 outside its 20 x 20; the portal hall authored 1,349,
        # all inside its 37.4 x 37.4, reaching 25.5 m from the centre -- so
        # the pad does fill its corners and the rectangle is the right SHAPE,
        # not a disc.  A pad levels its rectangle AND feathers past it, so a
        # rectangle-only test misses a third of what the pad laid, and "a
        # pad's written extent is not its nominal radius" is what the T4
        # clobber cost to learn.  The rectangle catches ground the pad owns
        # and has not levelled yet; the levelled set catches ground it
        # levelled outside its rectangle.  Neither contains the other.
        hits: dict[str, list] = {}
        for (sx, sz), (lvl, sm) in ours.items():
            guards = [q for q in near
                      if abs(sx - q["x"]) <= q["hw"]
                      and abs(sz - q["z"]) <= q["hd"]]
            for line, laid in pad_authored.items():
                q = pad_lines[line]
                if (sx, sz) in laid and q not in guards:
                    guards.append(q)
            if not guards:
                continue
            held = prior_owner.get((sx, sz))
            for q in guards:
                was = pad_authored.get(q["file_line"], {}).get((sx, sz)) \
                    or prior_state.get((sx, sz))
                if was is not None and abs((lvl + sm) - (was[0] + was[1])) \
                        <= FLATTEN_TOLERANCE_M:
                    continue  # carried forward verbatim: the ground stays put
                inside = (abs(sx - q["x"]) <= q["hw"]
                          and abs(sz - q["z"]) <= q["hd"])
                key = (f"{q['name']}#{q['seq']} ({q['role']}, site "
                       f"{q['site_id']}, {2 * q['hw']:.1f} x {2 * q['hd']:.1f} m "
                       f"at ({q['x']:.1f}, {q['z']:.1f}))")
                moved = ((lvl + sm) - (was[0] + was[1])) if was else (lvl + sm)
                hits.setdefault(key, []).append((sx, sz, moved, held, inside))
        for key, pts in sorted(hits.items()):
            worst = max(pts, key=lambda t: abs(t[2]))
            shown = ", ".join(f"({x}, {z})" for x, z, _, _, _ in pts[:6])
            # ATTRIBUTE EVERY STEP TO THE CLAIM THAT OWNS THE SAMPLE.  The
            # rectangle names the FOOTPRINT's owner; the value that is about
            # to be overwritten may belong to a third claim that wrote the
            # same square metre later, and naming the wrong one sends the
            # next agent to the wrong record.  MEASURED cost of getting this
            # backwards: a 3.676 m wall attributed to T12 that was T3's own
            # carriageway edge.
            from collections import Counter as _C
            held = _C(f"{h['name']}#{h['seq']} ({h['role']})" if h else
                      "NOBODY (unwritten ground inside the footprint)"
                      for _, _, _, h, _ in pts)
            bad.append(
                f"terrain_write ({p.get('name')}, {p.get('role')}) AUTHORS "
                f"{len(pts)} sample(s) INSIDE the pad footprint of {key}: "
                f"{sum(1 for t in pts if t[4])} within its rectangle, "
                f"{sum(1 for t in pts if not t[4])} on ground the pad itself "
                f"levelled outside that rectangle. "
                f"Worst move {worst[2]:+.3f} m at ({worst[0]}, {worst[1]}); "
                f"first at {shown}. Those samples are currently held by "
                f"{dict(held.most_common(4))}. Inside a settlement pad the "
                f"pad wins -- "
                f"it is a building's foundation, and regrading it is the "
                f"defect this refusal exists for. A road grades into a pad's "
                f"EDGE and never authors a sample inside it. Nothing is "
                f"clamped and nothing is dropped for you: stop writing those "
                f"samples (compose the prior state from the claims in FILE "
                f"ORDER, excluding this claim's own earlier writes, so the "
                f"pad's samples are inherited from the PAD's record and not "
                f"from a laundered copy), re-plan the segment, or have the "
                f"pad's owner re-emit.")
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
