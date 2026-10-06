#!/usr/bin/env python3
"""The append-only writer, the blob store, and the environment fingerprint.

    from ledger import Ledger
    led = Ledger.open(worldseed.world(), actor="RoadNet")   # never a literal
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
if str(HERE.parent) not in sys.path:
    sys.path.append(str(HERE.parent))
import worldseed  # noqa: E402

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


def _stat_size_settled(worlds: Path, chosen: Path | None) -> int | None:
    """Size of the newest db2.  The server may rotate it away between
    choosing and stat-ing; re-resolve by generation rather than crash a build
    unit (MEASURED: a live Fresh42 unit died here on _main.9.db2)."""
    for _ in range(40):
        if chosen is None:
            return None
        try:
            return chosen.stat().st_size
        except FileNotFoundError:
            time.sleep(0.25)
            chosen = worldseed.newest_save(worlds, "db2")
    raise FileNotFoundError(f"{worlds}: newest _main.*.db2 never settled")


# WORLD_MANIFEST_DEPENDS_ON: the world whose save is being fingerprinted, and
# nothing else.  This default read `world: str = "Ulfsland"` -- a CONSTANT
# standing for a quantity that depends on the run, the same shape as
# `LiveBuilder(world="Ulfsland")` (36 of 40 callsites passing none) and
# `applied.Applied(world="Ulfsland")` (17 of 19).  `worldseed.world()` is
# argument > $VH_WORLD > "Ulfsland", so an unset environment resolves exactly
# what the literal did and NO existing caller moves.
def fingerprint(world: str = "", *, game_build_anchor: str | None = None,
                heavy: bool = True, probe_modifiers: bool = True) -> dict:
    """The `world_manifest` payload, measured from the host.

    `heavy=False` skips hashing 120 DLLs, for a caller that only wants the
    world identity.  A manifest written with heavy=False records that, so a
    replay does not mistake "not measured" for "no mods".
    """
    world = world or worldseed.world()
    base = worldseed.world_root() / world
    worlds = base / "config_merged" / "worlds_local" / world
    newest_fwl = worldseed.newest_save(worlds, "fwl2")
    wf = read_fwl(newest_fwl) if newest_fwl else {}
    newest_db = worldseed.newest_save(worlds, "db2")
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
        # THE WORLD'S OWN IDENTITY.  The file hash above is not stable for a
        # world's life (Ulfsland's live `.fwl2` no longer hashes to what its
        # ledger pinned) and two worlds can share a name and a seed, so a
        # reader asking "is this save the one this ledger is about" needs the
        # uid.  `accept/instruments.py::require_save_is_the_ledgers`.
        "world_uid": wf.get("uid"),
        "db_bytes": _stat_size_settled(worlds, newest_db),
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

# Whether `_protected_pieces` may read a record's OWN `wire` when the plan
# blob was never stored.  ALWAYS TRUE IN PRODUCTION.  It exists as a named
# switch so the gate's case can restore the defect it fixes -- MEASURED on
# this chain: with it False, 79 of 79 `bridge_body_place` records recover
# ZERO pieces and the flatten guard falls back to discs of 26-33 m; with it
# True the same 79 recover 19,595 exact piece positions.
READ_RECORD_WIRE = True

# WHAT A KEEP-OUT IS A FUNCTION OF.  Declared, so that a neuter can take one
# term away and the gate can require the answer to move.
#
#   kind            `params.flatten`: WHOSE hazard this keep-out protects.
#   site_evidence   the pieces/discs `_site_evidence` recovers for the site.
#   retirement      a retired piece is not standing there.
#   discharge       `terrain_write.params.reseats`: the debt a write accepts
#                   when it moves ground a RESEAT claim stands on.
KEEP_OUT_DEPENDS_ON = ("kind", "site_evidence", "retirement", "discharge")

# THE TWO KINDS, AND THE DIFFERENCE IS WHERE THE HAZARD LIVES.
#
# `FORBIDDEN`  THE GROUND IS THE HAZARD.  A bridge pile stands on the
#              generated bed of a channel: cut it and the water the piles are
#              driven into is gone, and no tool in this tree can put water
#              back.  The ground must not move, full stop.
# `RESEAT`     THE PIECE IS THE HAZARD, AND IT IS RECOVERABLE.  A roadside
#              lamp's only claim on the ground is that it must not be left
#              hanging in the air above it -- and `roads/relight.py` retires
#              and re-spawns lamps as a recorded, verified operation, so the
#              claim is DISCHARGEABLE by re-seating rather than by freezing.
#
# WHY THIS DISTINCTION EXISTS, MEASURED ON ULFSLAND save bc484171e3039bc0.
# Four of that world's nine impassable road steps -- N4-mid2-terminus
# stations 2962/2964/2965/2967, rises 2.107 / 2.416 / 2.538 / 3.041 m against
# a 0.7813 m slide limit -- came back `1A_FORBIDDEN` owned by
# `road_lighting seq 2784`, A LAMP.  Its own `flatten_reason` reads "lamps
# stand ON the road the segment already graded. A terrain_write here would
# re-level ground the road owns", which is a statement about the ROAD'S
# interest and not about any hazard the lamp carries, asserted at a point in
# the pipeline where NOTHING HAS PROVEN THE ROAD WALKABLE: `buildstage.py`
# declares `roadside` (step 7) after `road_ground` (4) and the only step that
# re-reads the graded corridor is `recheck` (8), AFTER the lamps -- MEASURED
# from BUILD_STEPS by FinishFresh, 2026-09-23.  So a lamp froze a 3.041 m
# step in a carriageway and step 8 could only report it.
#
# THE REACH IS NOT THE DEFECT AND SHRINKING A RADIUS CANNOT FIX IT.  A piece
# contributes a disc of radius 0.0 and is probed EXACTLY, and 338 of this
# world's 388 corridor torches stand at half_width + 0.55 m -- on the
# SHOULDER, outside the carriageway.  What reaches across the carriageway is
# the REPAIR: a cone that lowers a vertex by `d` must touch out to
# `d / slide_limit`, so a 2.26 m overage reaches 4.3 m and unavoidably lands
# on the shoulder the lamp stands on.  The lamp's claim is real -- cut the
# road and the lamp hangs 2.26 m in the air -- but it is a claim ON ITS OWN
# SEATING, and the answer to it is to re-seat the lamp, not to make the road
# permanently unwalkable.
KEEP_OUT_KINDS = ("FORBIDDEN", "RESEAT")

# NEUTER SWITCHES, NEVER FALSE IN PRODUCTION.  Each takes ONE named term out
# of `KEEP_OUT_DEPENDS_ON`, in opposite directions, so the gate's cases can
# put each half of the defect back and be required to come out the other way.
#
#   READ_KEEP_OUT_KIND     False -> every keep-out reads FORBIDDEN (the
#                          `kind` term is lost, the keep-out WIDENS, and the
#                          lamp's spurious freeze RETURNS).
#   READ_RESEAT_DISCHARGE  False -> a RESEAT claim is ignored outright (the
#                          `discharge` term is lost, the keep-out NARROWS,
#                          and a write may move a lamp's ground without ever
#                          naming the debt).
# A bridge's FORBIDDEN keep-out must REFUSE under BOTH: it depends on neither
# term, and it is the denominator no neuter here can patch.
READ_KEEP_OUT_KIND = True
READ_RESEAT_DISCHARGE = True


def keep_out_kind(p: dict) -> str | None:
    """Which kind of keep-out this record declares, or None for no keep-out.

    NONE MAY NEVER STAND FOR AN ANSWER: an UNRECOGNISED value is read as
    `FORBIDDEN`, not as "no keep-out".  A typo in an emitter must cost a
    refused write, never a silently unprotected structure.
    """
    v = p.get("flatten")
    if not isinstance(v, str) or not v:
        return None
    if not READ_KEEP_OUT_KIND:
        return "FORBIDDEN"
    return v if v in KEEP_OUT_KINDS else "FORBIDDEN"


def declared_reseats(rec: dict) -> set:
    """The seqs a `terrain_write` DECLARES it will re-seat.

    Reads the record only.  The `READ_RESEAT_DISCHARGE` neuter belongs at the
    point where the CLAIM is weighed, not here: emptying this set would make
    the guard refuse MORE, and the direction this neuter has to restore is
    the one where a RESEAT claim stops refusing anything at all.
    """
    out = set()
    for s in ((rec.get("params") or {}).get("reseats") or []):
        try:
            out.add(int(s))
        except (TypeError, ValueError):
            continue
    return out

# WHICH OPS STATE WHERE A SITE IS, as opposed to where one thing IN it is.
# `zones_generate` is the ground the site reserved, `objects_clear` the
# cylinder it cleared, `spawn_plan` the body's anchor -- each one is about the
# SITE.  `portal` and `spawn` are about a single arch or a single sign, and
# reading one of those as the site's position moves the portal hall's whole
# 37.4 x 37.4 m footprint 8.9 m.  See `_pad_footprint`.
_SITE_EXTENT_OPS = ("zones_generate", "objects_clear", "spawn_plan")



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


# ---------------------------------------------------------------------------
# SIGNAGE OUTLIVING ITS DESTINATION
#
# THE DEFECT, MEASURED ON ULFSLAND.  `PortalTriage` dropped seven tags at
# seq 2510-2523 by retiring BOTH ends of each - `prefab: portal_wood`, one
# record per end, correct and complete for the arches.  Four of those tags
# were named by a standing guidepost board, each board emitted by the SAME
# pass that placed the arch and each carrying the tag in its own
# `meta.tag`: 'Tree Near u-treenear' (-612.00, -232.11), 'Wt Spawn
# u-wtspawn' (111.30, -102.66), 'Wt South u-wtsouth' (533.30, 47.34), 'Wt
# Town u-wttown' (801.30, 729.34).  Nothing retired the boards, so the
# world kept four signposts pointing at arches that are not there, and the
# gate's `portals` section refuses all four.
#
# It is NOT four typos.  The cascade was computable from the ledger alone
# at the moment of the drop - the sign spawn records name the tag - and no
# rule required anyone to compute it.  This is that rule: a retire that
# takes a tag's LAST live end is refused while signage naming that tag is
# still standing.  A re-seat (retire one end, re-place it at the same tag)
# is untouched, because the tag still has an end.
# ---------------------------------------------------------------------------
PORTAL_PREFAB = "portal_wood"
SIGNAGE_PREFABS = ("sign", "sign_notext")


def _pos3(p: dict) -> tuple | None:
    pos = p.get("pos")
    if isinstance(pos, (list, tuple)) and len(pos) == 3:
        return (float(pos[0]), float(pos[1]), float(pos[2]))
    return None


def _retire_removes(rec: dict, prior: list[dict]) -> set[int]:
    """Indices into `prior` this retire record removes.

    INDICES AND NOT `seq`: the chain forked at file line 47 and carries
    duplicate seq labels, so `seq` is not a total order on this artefact -
    `applied.py`, `lamps.segments_from_ledger` and this function all use
    file order for the same reason.
    """
    p = rec["params"]
    named = {int(s) for s in p.get("retires", []) or ()}
    out = {i for i, q in enumerate(prior) if q.get("seq") in named and named}
    pos, pf = _pos3(p), p.get("prefab")
    if pos is None or not pf:
        return out
    r = max(float(p.get("radius_m") or 0.0), 0.01)
    for i, q in enumerate(prior):
        if q["op"] not in ("spawn", "portal"):
            continue
        qp = q["params"]
        if qp.get("prefab") != pf:
            continue
        qpos = _pos3(qp)
        if qpos is not None and math.dist(pos, qpos) <= r:
            out.add(i)
    return out


def _tag_of(rec: dict) -> str:
    """The portal tag a record carries, from its params or its meta."""
    p, m = rec.get("params") or {}, rec.get("meta") or {}
    for v in (p.get("tag"), (p.get("zdo_strings") or {}).get("tag"),
              m.get("tag")):
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def live_portal_ends(records: list[dict]) -> dict[str, list[int]]:
    """tag -> indices of the portal records still standing, in file order."""
    dead: set[int] = set()
    for i, r in enumerate(records):
        if r["op"] == "retire":
            dead |= {j for j in _retire_removes(r, records[:i])}
    out: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        if r["op"] != "portal" or i in dead:
            continue
        tag = _tag_of(r)
        if tag:
            out.setdefault(tag, []).append(i)
    return out


def live_signage_naming(records: list[dict], tag: str) -> list[dict]:
    """Every standing sign this ledger records as naming `tag`.

    A sign NAMES a tag when its own record says so (`meta.tag`, which the
    guidepost emitter writes) or when the tag appears verbatim in the text
    it was spawned with - which is what a player reads and therefore the
    only other definition worth having.
    """
    dead: set[int] = set()
    for i, r in enumerate(records):
        if r["op"] == "retire":
            dead |= {j for j in _retire_removes(r, records[:i])}
    out = []
    for i, r in enumerate(records):
        if r["op"] != "spawn" or i in dead:
            continue
        p = r["params"]
        if p.get("prefab") not in SIGNAGE_PREFABS:
            continue
        text = ((p.get("zdo_strings") or {}).get("text") or "")
        if (r.get("meta") or {}).get("tag") == tag or tag in text:
            out.append(dict(seq=r.get("seq"), file_index=i,
                            pos=_pos3(p), text=text.splitlines()[:1]))
    return out


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


def parse_spawn_wire(text: str, declared: int | None = None
                     ) -> list[tuple[str, float, float]] | None:
    """`(prefab, x, z)` for every `spawn_object` in a wire or a plan blob, or
    None when the text cannot be trusted to have been read WHOLE.

    ONE READER OF THIS FORMAT, AND THE COUNT IS THE REASON IT IS ONE.
    MEASURED on 2026-09-21, this tree held FOUR independent implementations of
    "what did this record command", and THREE of them were wrong in the same
    way - they matched an anchored pattern per LINE, and the wire is
    SEMICOLON-PACKED:

      `settlements`' declaration index      13,138 -> 24,351 placements (+11,213)
      `structures::plan_commands`           226 of 421 records mis-sized
      `writer::_protected_pieces`           21 of 289 blobs short; lh-north
                                            read 3 of 64 PROTECTED pieces, 4.7%,
                                            and that set gates every builder's
                                            keep-out and the terrain-write guard
      `network/verify_site.py`              same ceiling, cosmetic, named

    A wire format with a packed form and a line-oriented reader grows a new
    wrong copy every time somebody needs the answer somewhere else.  So this
    is the reader; `roads/relight.py` and `accept/sections/structures.py` call
    it, and anything that needs to parse a spawn wire calls it rather than
    writing a fifth.

    `declared` IS THE ACCOUNTING IDENTITY, not decoration: a record's own
    `params.command_count` says how many commands it sent, so a parse that
    does not produce that many has not read the text, and a short read must
    never be able to look like a small body.  Passing it makes a disagreement
    UNREADABLE (None) instead of quietly short; omitting it says the caller
    has no declaration to check against and accepts what is there.

    `pos=` IS z,x,y - `Parse::VectorZXYRange` - so field 0 is Z and field 1 is
    X.  MEASURED twice independently (`to_rcon_plan.pos_arg`'s IL-read
    docstring, and RoadBuild cross-checking three structures' plan fields
    against their own `expect.prefab_count`), and it cannot be caught
    downstream: swapping x and z for every point is an ISOMETRY, so every
    distance, every cluster and every extent is unchanged by a transposed
    read.  It has to be right here.
    """
    out: list[tuple[str, float, float]] = []
    for raw in text.splitlines():
        for cmd in raw.split(";"):
            m = _SPAWN_POS_RE.match(cmd.strip())
            if not m:
                continue
            try:
                z, x = float(m.group(2)), float(m.group(3))
            except ValueError:
                continue
            out.append((m.group(1), x, z))
    if isinstance(declared, int) and declared > 0 and len(out) != declared:
        return None
    return out


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

    AND THE EVIDENCE IS NOT ONLY IN THE BLOBS.  MEASURED on this chain
    2026-09-22: EVERY `bridge_body_place` record -- 23 of them, every decked
    crossing this project has ever built -- carries `requires.blobs: []` and a
    `plan_sha256` of a blob NOBODY EVER STORED, because `boardwalk` hashes the
    wire and hands the wire itself to `emit` inline.  `emit` persists it: the
    record holds `wire` as a literal list of 48 `spawn_object` commands.  This
    reader looked only at the blob store, found nothing, and returned None --
    so `_flatten_forbidden_check` fell back to the record's BOUNDING DISC,
    26.16 to 33.13 m, AND REFUSED EVERY LATER `terrain_write` INSIDE 2,150 TO
    3,450 m2 PER BRIDGE.  Three of one agent's nineteen validated road repairs
    were refused by that fallback tonight, one of them by a deck landed an hour
    earlier, and on the trajectory that was permanent: NOTHING COULD EVER BE
    GRADED NEAR ANY BRIDGE THIS PROJECT BUILDS.

    Fail-closed was right.  The missing evidence was not, and it was never
    missing -- it was in the record, in the field named `wire`, which is the
    same text the blob would have held.  The record is chained, so its own
    `wire` is no weaker a witness than a blob digest; and `command_count` is
    asserted against it exactly as it is against a blob, so a short read is
    still UNREADABLE rather than a small body.
    """
    p = rec.get("params", {})
    if rec.get("op") in ("spawn", "portal"):
        xz = _record_xz(p)
        return [(float(xz[0]), float(xz[1]))] if xz else None
    if rec.get("op") != "spawn_plan":
        return None
    shas = [p.get("plan_sha256")] + list(
        (rec.get("requires") or {}).get("blobs", []))
    texts: list[str] = []
    for sha in [s for s in shas if s]:
        try:
            texts.append(ledger.read_blob(sha).decode("utf-8", "replace"))
        except LedgerError:
            continue
    # THE RECORD'S OWN WIRE, LAST, so a stored blob still wins: the blob is
    # content-addressed and the inline copy is not, and where both exist they
    # are the same bytes by construction.
    #
    # `READ_RECORD_WIRE` exists so the gate's own case can put the defect
    # BACK and be required to come out the other way.  It is never set False
    # in production; a case that can only be shown to pass has not been shown
    # to be a case.
    inline = rec.get("wire") if READ_RECORD_WIRE else None
    if isinstance(inline, list) and inline:
        texts.append("\n".join(str(c) for c in inline))
    for text in texts:
        # SPLIT ON ';' BEFORE MATCHING, AND CHECK THE TOTAL.
        #
        # The blob is the literal command list and a command list is
        # SEMICOLON-PACKED: MEASURED on this chain, 21 of 289 spawn_plan
        # blobs pack many commands onto one line, and seq 2269-2284
        # `lh-north` declares 64 commands that this reader saw as THREE
        # because the pattern is `^`-anchored and was applied per line.  It
        # returned 4.7% of the pieces of a body whose whole purpose here is
        # to be the thing that must not move - and this function's own
        # docstring is an argument for pieces OVER a disc on the grounds
        # that the disc got all seven road segments wrong.  A reader that
        # silently returns 3 of 64 is worse than the disc, because it looks
        # like evidence.
        #
        # Same defect, same evening, in three separate readers of one wire
        # format: `settlements`' declaration index (11,213 placements
        # missing) and `structures`' plan reader.  `project.py::_commands`
        # already split on ';' and was the one that was right.
        # ONE READER, and the count asserted inside it: a blob that does not
        # yield the number of commands its own record declared is UNREADABLE,
        # and this function's contract already says None means "cannot
        # measure" rather than "nothing there" - the safe direction for a
        # guard that protects somebody else's building.
        got = parse_spawn_wire(text, p.get("command_count"))
        if got is None:
            return None
        pts = [(x, z) for _pf, x, z in got]
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

class _PriorDeltaField(_DeltaField):
    """The applied delta that is ALREADY on the compiler, for the same zones
    a `terrain_write` is about to replace.

    WHY THIS EXISTS, AND IT IS A CORRECTION RATHER THAN A RELAXATION.
    `_flatten_forbidden_check` says, in its own refusal text, that the write
    "would move the ground by X m" at a protected piece.  It computed X as
    the ABSOLUTE delta the new blob carries there.  Those are the same number
    only when the protected piece stands on ground nobody has written.

    MEASURED, 2026-09-19, the first time a write ever unioned a zone holding
    a road lamp: `road_T8_stenvik_wttown`'s own earlier write put -1.5462 at
    (812.5446, 741.55) and `Lampposts` seq 2221 then seated a lamp ON that
    graded road.  A zone holds exactly ONE `_TerrainCompiler` and this op
    deletes before it spawns, so any later write to zone (13,12) MUST union
    T8's samples forward VERBATIM or destroy them -- `_zone_clobber_check`
    requires exactly that.  The unioned blob therefore carries -1.5462 at the
    lamp, identical to the bit, and the old test read that as "would move the
    ground by 1.546 m" and refused.  The ground moves by 0.0000 m.  Two of
    this project's own invariants were in direct contradiction and the road
    that makes Ulfsgard reachable was the thing caught between them.

    So the guard now measures the CHANGE: the new blob's delta at the piece
    minus the delta the live compiler already holds there, composed from the
    prior `terrain_write` records in FILE ORDER with later writers winning
    per sample -- the same composition `roads/applied.py` documents and the
    same one the game performs, restricted to the zones this record touches
    because nothing outside them changes.

    NOT A WEAKENING, and provable in both directions:
      * a write that re-levels a protected piece's ground by any amount above
        `FLATTEN_TOLERANCE_M` still refuses, because the difference is
        non-zero;
      * a write that carries the existing ground forward unchanged passes,
        because the difference is zero;
      * a first write over a protected piece on natural ground still refuses,
        because the prior delta is 0 and the difference is the full delta.
    An unreadable prior blob is still fatal: it lands in `unreadable` and the
    caller refuses rather than assuming the ground was flat.
    """

    def __init__(self, ledger: "Ledger", rec: dict, prior: list[dict]):
        want = {(int(e["zone"][0]), int(e["zone"][1]))
                for e in rec["params"]["entries"]}
        self.zones: dict[tuple, tuple] = {}
        self.unreadable: set[tuple] = set()
        # FILE ORDER, not seq: the chain forked at file line 47 and carries
        # duplicate seq labels 43-46, so seq is not a total order on this
        # artefact.  `prior` is already in file order.
        for r in prior:
            if r.get("op") != "terrain_write":
                continue
            for e in r["params"].get("entries", []):
                key = (int(e["zone"][0]), int(e["zone"][1]))
                if key not in want:
                    continue
                try:
                    blob = ledger.read_blob(e["blob_sha256"])
                except LedgerError:
                    self.unreadable.add(key)
                    continue
                decoded = _decode_tcdata(blob)
                if decoded is None:
                    self.unreadable.add(key)
                    continue
                prev = self.zones.get(key)
                if prev is None:
                    self.zones[key] = (tuple(e["centre"]), decoded)
                    continue
                # Union per sample index, later writer wins -- which is what
                # the live compiler holds.
                (cx, cz), (pm, pl, ps) = prev
                modified, level, smooth = decoded
                pm = list(pm)
                pl = list(pl)
                ps = list(ps)
                for k in range(len(modified)):
                    if modified[k]:
                        pm[k], pl[k], ps[k] = modified[k], level[k], smooth[k]
                self.zones[key] = (tuple(e["centre"]), (pm, pl, ps))

class _ChangeField(_DeltaField):
    """How much a `terrain_write` MOVES the ground: new delta minus the
    delta already on the compiler, at any world position.

    Inherits `worst_delta_over_disc` unchanged, so the disc sweep and the
    exact-piece probe both ask the corrected question with no second
    implementation to drift.
    """

    def __init__(self, new: _DeltaField, prior: _DeltaField):
        self._new = new
        self._prior = prior
        self.zones = new.zones
        self.unreadable = set(new.unreadable) | set(prior.unreadable)

    def delta_at(self, x: float, z: float) -> float:
        return self._new.delta_at(x, z) - self._prior.delta_at(x, z)



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

    THE LAST SIBLING BEFORE THE WRITE, NOT THE FIRST, and reading it the
    other way round put a TWELFTH of this world's pad footprints on ground
    the building is not standing on.  A site that was NUDGED reserves its
    first choice and then reserves again: `stenvik-cottage-2` has a
    `zones_generate` at (484.7, 901.7) at seq 127 and a second at
    (477.7, 902.7) at seq 260, and its pad write (seq 265), its
    `objects_clear`, its `spawn_plan` and `settlements/placements/*.yaml` all
    say the SECOND.  Scanning forward returned the ABANDONED first attempt.

    MEASURED over this chain, claim centre against the position the site's
    own `spawn_plan`/`objects_clear` records: 12 of 32 resolvable pads were
    displaced, worst `tree-near-2` by 9.85 m, `stenvik-cottage-2` by 7.07 m,
    `stenvik-hut-1` by 5.00 m.  Every consequence is the defect this file
    keeps finding: the keep-out and this guard protected a rectangle nobody
    built on (unwritten ground a road is refused and no pad will ever lay --
    the annulus, displaced bodily), while the pad's REAL edge sat outside
    every rectangle and was protected only by per-sample authorship.

    AND IT MUST BE A RECORD ABOUT THE SITE, NOT ABOUT ONE THING IN IT.  Three
    ops state where the SITE is -- `zones_generate` (the ground it reserved),
    `objects_clear` (the cylinder it cleared) and `spawn_plan` (the body's
    anchor).  `portal` and `spawn` state where ONE MEMBER is, and taking one
    of those is how "the last sibling" alone gets the portal hall wrong: its
    `objects_clear` (seq 764) and its `spawn_plan` (seq 802) both say
    (-280.0, 216.0), and between them sit EIGHTEEN `portal` records, one per
    arch around a 26 m ring.  The last of them, seq 800 at
    (-271.279, 214.195), moved the hall's whole 37.4 x 37.4 m footprint 8.9 m
    east -- off the very edge, (-298.7, 214.0), that this session's headline
    defect is measured against.  So the scan is the LAST SITE-EXTENT RECORD
    at or before the write, which is both rules at once and neither alone.

    The last such sibling is the attempt the write belongs to, by
    construction: a later attempt has not happened yet when the pad is
    judged, and an abandoned earlier one has been superseded.
    """
    p = rec.get("params", {})
    if not _is_pad(p):
        return None
    sid = p.get("site_id")
    if not sid:
        return None
    pos = None
    for r in reversed(prior_through):
        if r.get("op") not in _SITE_EXTENT_OPS:
            continue
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
    def open(cls, world: str = "", *, actor: str,
             root: Path | None = None, manifest: dict | None = None,
             heavy_fingerprint: bool = True) -> "Ledger":
        # LEDGER_ROOT_DEPENDS_ON: the world being written.  The default was
        # the literal "Ulfsland", i.e. THE ONE UNTOUCHABLE WORLD IN THIS
        # PROJECT sitting behind an omitted argument.  MEASURED 2026-09-24:
        # 0 of the 23 `Ledger.open(` callsites in this tree omit the world,
        # so the live exposure of this default is ZERO -- it is a trap that
        # has not yet been stepped in, not a defect with a body.  Closed
        # anyway, because the cost of the next caller writing
        # `Ledger.open(actor=...)` is 26,619,627 bytes of quarantined chain.
        world = world or worldseed.world()
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
            bad += self._reseat_debt_check(rec, prior)
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

        if op == "retire" and p.get("prefab") == PORTAL_PREFAB:
            bad += self._signage_orphan_check(rec, prior)

        return bad

    def _signage_orphan_check(self, rec: dict, prior: list[dict]) -> list[str]:
        """No dropping a tag's last arch while its signpost still stands.

        MEASURED, live on Ulfsland: seq 2510-2517 retired both ends of
        u-treenear, u-wtsouth, u-wtspawn and u-wttown and left all four
        guidepost boards standing, each still reading the tag it was
        emitted with.  The boards' own spawn records carry `meta.tag`, so
        the cascade was computable from this file at the moment of the drop
        and no rule asked for it.

        The predicate is the LAST END, not any end: a re-seat (seq 773-778
        retired four hub ends and re-placed them at the same tags) leaves
        the tag live and its board correct, and must not be refused.
        """
        p = rec["params"]
        pos = _pos3(p)
        if pos is None:
            return []
        # WHICH TAG THIS RECORD IS TAKING DOWN.  Its own meta first - the
        # drop pass writes it - then the portal record standing at the
        # position it names, because a retire that names only a position
        # must still be answerable.
        tag = _tag_of(rec)
        removed = _retire_removes(rec, prior)
        if not tag:
            for i in sorted(removed):
                if prior[i]["op"] == "portal":
                    tag = _tag_of(prior[i])
                    if tag:
                        break
        if not tag:
            return []
        ends = live_portal_ends(prior).get(tag, [])
        remaining = [i for i in ends if i not in removed]
        if remaining:
            return []
        boards = live_signage_naming(prior, tag)
        if not boards:
            return []
        where = ", ".join(
            f"{(b['text'] or [''])[0]!r} at "
            f"({(b['pos'] or (0, 0, 0))[0]:.2f}, "
            f"{(b['pos'] or (0, 0, 0))[2]:.2f}) seq {b['seq']}"
            for b in boards)
        return [
            f"this retire takes the LAST live end of portal tag {tag!r} "
            f"({len(ends)} end(s) known, {len(removed)} removed here, 0 "
            f"left) while {len(boards)} sign(s) this ledger placed still "
            f"NAME that tag: {where}.  A board pointing at an arch that is "
            f"not there is a signpost to nowhere, and the gate's `portals` "
            f"section refuses one per board.  Retire the signage in this "
            f"same pass - or re-place the arch - and then drop the tag."]

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

    def _reseat_debt_check(self, rec: dict, prior: list[dict]) -> list[str]:
        """A `reseats` declaration MUST name a live RESEAT claim, and nothing
        else.

        THE DISCHARGE IS THE ONLY WAY PAST A KEEP-OUT IN THIS FILE, so it is
        the only thing worth forging, and every way of forging it is named
        here rather than left to the next reader:

          * naming a `FORBIDDEN` seq -- laundering a bridge pile into a
            re-seatable lamp.  That is exactly the owner exception this
            guard has never had and must not grow: a deck's keep-out
            refuses its OWN owner, and that is the property that stopped a
            deck burying its own tiles.
          * naming a retired seq, or a seq that is not in the chain --
            a promise about nothing, which reads as a discharge.
          * naming a seq this write never touches -- harmless, and still
            refused, because a declaration that is not about this write is
            a habit that makes the honest ones unreadable.

        Nothing is loosened by refusing these: a write with no `reseats`
        key reaches `_flatten_forbidden_check` exactly as before.
        """
        declared = declared_reseats(rec)
        if not declared:
            return []
        bad: list[str] = []
        gone = retired_seqs(prior)
        by_seq = {r.get("seq"): r for r in prior}
        zones = {tuple(e["zone"]) for e in rec["params"]["entries"]}
        for s in sorted(declared):
            r = by_seq.get(s)
            if r is None:
                bad.append(
                    f"terrain_write declares reseats={s}, and this chain has "
                    f"no record at that seq. A discharge is the only thing "
                    f"that gets a write past a keep-out; one that names "
                    f"nothing is a promise about nothing.")
                continue
            if s in gone:
                bad.append(
                    f"terrain_write declares reseats={s}, which is RETIRED. "
                    f"There is nothing standing there to re-seat, so this "
                    f"declaration discharges a claim that no longer exists.")
                continue
            kind = keep_out_kind(r.get("params") or {})
            if kind != "RESEAT":
                bad.append(
                    f"terrain_write declares reseats={s}, but seq {s} "
                    f"({(r.get('params') or {}).get('role')}) declares "
                    f"flatten={kind!r}, not RESEAT. Only a structure whose "
                    f"claim is on its OWN SEATING may be discharged by a "
                    f"promise to put it back; a FORBIDDEN keep-out protects "
                    f"the GROUND -- cut the bed under a pile and no tool in "
                    f"this tree puts the water back. There is no owner "
                    f"exception here and this is not one.")
                continue
            if not self._keep_out_reaches(r, prior, zones):
                bad.append(
                    f"terrain_write declares reseats={s} "
                    f"({(r.get('params') or {}).get('role')}), and no piece "
                    f"or disc of that site reaches any zone this write "
                    f"claims ({sorted(zones)}). A discharge that is not "
                    f"about this write is noise, and noise is what makes "
                    f"the honest declarations unreadable.")
        return bad

    def _keep_out_reaches(self, protected: dict, prior: list[dict],
                          zones: set) -> bool:
        """Does any piece or disc of `protected`'s SITE reach one of `zones`?

        THE SAME TRIGGER `_flatten_forbidden_check` USES, and deliberately
        the same function, so a discharge can never be about a site the
        guard would not have asked about.  Zone-granular and cheap: no blob
        is decoded, because this answers "is this declaration about this
        write" and not "does this write move that ground".
        """
        import math
        pieces, site_discs = _site_evidence(protected, prior, self)
        discs = ([(px, pz, 0.0) for px, pz in pieces] if pieces
                 else site_discs)
        if not discs:
            xz = _record_xz(protected.get("params") or {})
            if xz is None:
                return False
            discs = [(float(xz[0]), float(xz[1]), 0.0)]
        for dx, dz, _rad in discs:
            zx = math.floor((float(dx) + 32.0) / 64.0)
            zz = math.floor((float(dz) + 32.0) / 64.0)
            if {(zx + i, zz + j) for i in range(-1, 2)
                    for j in range(-1, 2)} & zones:
                return True
        return False


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
                     if keep_out_kind(r["params"]) is not None
                     and r["seq"] not in retired]
        discharged = declared_reseats(rec)
        # WHAT THIS GUARD ACTUALLY READ, recorded into the record itself.
        #
        # MEASURED by PeakFix / VestvikRoad / HarbourBuild-2, 2026-09-19: this
        # function has TWO paths to a clean return and they were reported with
        # the same silence.  Path (a): no FORBIDDEN record's extent reaches any
        # zone this write touches, the `reach & zones` trigger at the bottom
        # never fires, `field` is NEVER DECODED, and the guard returns []
        # having measured nothing.  Path (b): the trigger fires, the change
        # field is built and every piece and disc is probed, and the guard
        # returns [] having measured something.  Both read as PASS.
        #
        # That ambiguity was not academic.  R3-n0bjunction-vestvik took path
        # (a) -- 0 of 285 live FORBIDDEN records reach its zones -- and its
        # clean return was quoted as evidence the write had been checked.  R4
        # took path (b) with 25 triggers behind it.  Same word, two different
        # facts.  And the failing direction is worse: an un-registered blob
        # produces a REFUSAL naming an over-water structure the author has
        # never heard of, which reads as a real violation.
        #
        # So the guard now states its population the way every other number in
        # this project must: instrument, count, and what it looked at.  It is
        # written into `rec["meta"]` because the record is the only place a
        # reader will still have it a week from now, and because a verdict
        # whose provenance lives in a transcript is a verdict without one.
        by_kind: dict = {}
        for r in forbidden:
            k = keep_out_kind(r["params"]) or "?"
            by_kind[k] = by_kind.get(k, 0) + 1
        census = {"forbidden_live": len(forbidden), "reached_this_write": 0,
                  "triggered_by_seq": [], "field_decoded": False,
                  "keep_out_live_by_kind": dict(sorted(by_kind.items())),
                  "reseats_declared": sorted(discharged),
                  "reseats_honoured": [],
                  "why": "reached_this_write is the number of live "
                         "keep-out records whose readable extent "
                         "touches a zone this write claims. 0 means this "
                         "guard measured NOTHING and its pass is vacuous -- "
                         "safe, but not evidence. Non-zero with "
                         "field_decoded true means every one of those "
                         "records' pieces and discs was probed against this "
                         "write's own TCData. keep_out_live_by_kind splits "
                         "them: FORBIDDEN is ground that must not move, "
                         "RESEAT is a piece that must not be LEFT hanging "
                         "and whose claim a write discharges by naming the "
                         "seq in params.reseats -- see KEEP_OUT_KINDS."}
        meta = rec.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["flatten_guard"] = census
        if not forbidden:
            return bad
        zones = {tuple(e["zone"]) for e in rec["params"]["entries"]}
        field = None  # decoded lazily: only needed once a trigger fires

        for r in forbidden:
            p = r["params"]
            kind = keep_out_kind(p)
            # A RESEAT CLAIM THIS WRITE HAS ACCEPTED IS DISCHARGED, AND
            # NOTHING ELSE IS.  The write NAMED this seq in `params.reseats`,
            # which is a recorded, chained promise to put the piece back on
            # the surface it is about to create -- `roads/relight.py` is the
            # tool that keeps it, and `_reseat_debt_check` refuses a
            # declaration that names anything else.  This is NOT an owner
            # exception: a deck declares FORBIDDEN and still refuses ITS OWN
            # owner, which is the property that stopped a deck burying its
            # own tiles and must not be re-opened.
            # AND `READ_RESEAT_DISCHARGE` FALSE RESTORES THE OTHER HALF OF
            # THE DEFECT: the claim is dropped outright and nothing has to be
            # named, which is the shape a keep-out takes when it refuses
            # nothing.  Never False in production.
            if kind == "RESEAT" and (not READ_RESEAT_DISCHARGE
                                     or r["seq"] in discharged):
                if r["seq"] not in census["reseats_honoured"]:
                    census["reseats_honoured"].append(r["seq"])
                continue
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
                        f"the protected structure at seq {r['seq']} "
                        f"({p.get('role')}) declared flatten={kind}: "
                        f"{p.get('flatten_reason', 'no reason recorded')}. "
                        f"That record carries NO measurable extent (no "
                        f"`expect.prefab_count` with pos+max), so the precise "
                        f"test cannot run and the whole zone is refused. Add "
                        f"the structure's disc to its record to get a "
                        f"footprint-granular answer."
                        + ("" if kind != "RESEAT" else
                           f" This is a RESEAT claim: it is dischargeable, "
                           f"but not blind -- name seq {r['seq']} in "
                           f"params.reseats only once its extent is readable "
                           f"and you can put the pieces back."))
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
                census["reached_this_write"] += 1
                if r["seq"] not in census["triggered_by_seq"]:
                    census["triggered_by_seq"].append(r["seq"])
                if field is None:
                    # THE CHANGE, NOT THE ABSOLUTE DELTA.  The refusal below
                    # says "would move the ground by"; this is the field that
                    # actually measures that.  See `_PriorDeltaField` for the
                    # measurement that forced the correction -- a union that
                    # carries -1.5462 forward to the bit under a lamp moves
                    # the ground by 0.0000 m and used to be refused as 1.546.
                    field = _ChangeField(_DeltaField(self, rec),
                                         _PriorDeltaField(self, rec, prior))
                    census["field_decoded"] = True
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
                        f"not a flat one. WHAT THIS GUARD READ: "
                        f"{census['reached_this_write']} of "
                        f"{census['forbidden_live']} live FORBIDDEN records "
                        f"reach this write's zones; the unreadable bytes are "
                        f"THIS record's own, named in "
                        f"entries[].blob_sha256. MEASURED cause on two of "
                        f"three occurrences: the blobs were not yet in the "
                        f"store. Register them with `Ledger.blob` BEFORE "
                        f"pre-flighting this guard -- a refusal that names a "
                        f"structure you have never heard of is usually this, "
                        f"not a violation.")
                    continue
                if radius <= 0.0:
                    # A piece: probe it exactly. `delta_at` is the bilinear
                    # blend Heightmap renders, so an untouched piece reads
                    # exactly 0.0 and there is nothing to sweep.
                    worst, at = abs(field.delta_at(dx, dz)), (dx, dz)
                else:
                    worst, at = field.worst_delta_over_disc(dx, dz, radius)
                if worst > FLATTEN_TOLERANCE_M:
                    where = (f"terrain_write would move the ground by "
                             f"{worst:.3f} m at ({at[0]:.1f}, {at[1]:.1f}), "
                             + (f"AT A PIECE of the protected structure "
                                if radius <= 0.0 else
                                f"inside the {radius:.1f} m footprint of the "
                                f"protected structure ") +
                             f"at seq {r['seq']} ({p.get('role')}) declared "
                             f"flatten={kind}: "
                             f"{p.get('flatten_reason', 'no reason recorded')}. ")
                    if kind == "RESEAT":
                        # DISCHARGEABLE, AND THE REFUSAL SAYS HOW.  The claim
                        # is on the PIECE's seating, not on the ground, so it
                        # is answered by putting the piece back -- and the
                        # write must SAY SO, in the chain, before it lands.
                        bad.append(
                            where +
                            f"That is a RESEAT claim: what this structure "
                            f"owns is its own SEATING, not this ground, and "
                            f"a cut here leaves it hanging {worst:.3f} m in "
                            f"the air. Discharge it -- put `{r['seq']}` in "
                            f"this write's `params.reseats` and re-seat the "
                            f"pieces afterwards (roads/relight.py retires "
                            f"and re-spawns lamps as one recorded "
                            f"operation). Declaring it without re-seating "
                            f"leaves a floating piece the structures "
                            f"grounding census will find.")
                    else:
                        bad.append(
                            where +
                            f"Levelling there removes the water the "
                            f"structure stands over -- the early-dock "
                            f"defect. Move the route, or stop writing "
                            f"samples inside that disc; there is no flag "
                            f"that waives this.")
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

        # A PAD'S FOOTPRINT IS THE GROUND IT ITSELF LEVELLED -- and the
        # DECLARED RECTANGLE only for a pad that has levelled NOTHING.
        #
        # MEASURED on this ledger, per pad, over each pad's own AUTHORED
        # samples: `stenvik-stonehouse-1` seq 272 authored 165 and only 60 of
        # them are inside its 11.0 x 15.0 m rectangle; `wt-town` seq 387
        # authored 17 outside its 20 x 20; the portal hall authored 1,349,
        # all inside its 37.4 x 37.4, reaching 25.5 m from the centre.  A pad
        # levels its rectangle AND feathers past it, so the levelled set is
        # the wider half where it matters and "a pad's written extent is not
        # its nominal radius" is what the T4 clobber cost to learn.
        #
        # THE OTHER HALF -- "the rectangle catches ground the pad owns and
        # has not levelled yet" -- WAS THE UNWRITTEN ANNULUS, and it is
        # removed here.  `_pad_footprint` half-extents are
        # `pad_w / 2 + apron_m`, strictly wider than what any pad writes:
        # MEASURED over this chain, 21,666 lattice samples lie inside the 58
        # live pad rectangles and 3,564 have never been levelled by any pad
        # (1,355 in `sudrberg-keep`, 666 of `stenvik-hall-1`'s 676).  This
        # guard refused a road those samples and the pad never wrote them, so
        # no participant could repair them -- the pit at (-299, 214) beside
        # the portal hall is one, and it cost 28 of 28 portal destinations.
        # There is no floor on ground nobody levelled, so there is no floor
        # to regrade; a PIECE standing there is protected by the piece gates,
        # which measure the piece instead of a rectangle it sits in.
        # `ribbon.PadKeepOut` applies the identical rule, so the rasteriser
        # and this guard cannot disagree about what a foundation is.
        #
        # "LEVELLED NOTHING" IS ASKED OF THE PAD'S OWN RECORD, not of the
        # samples this write happens to hold.  `pad_authored` is scoped to
        # `ours`, so a pad whose floor lies just outside this write would
        # read as unlaid and its rectangle would gate again -- the annulus,
        # reintroduced as a neighbourhood artefact.
        laid_sites: set[str] = set()
        for q in near:
            rp = prior[q["file_line"]]["params"]
            for e in rp.get("entries", []):
                got = _entry_samples(e, self)
                if got:
                    laid_sites.add(q["site_id"])
                    break
        hits: dict[str, list] = {}
        for (sx, sz), (lvl, sm) in ours.items():
            guards = [q for q in near
                      if q["site_id"] not in laid_sites
                      and abs(sx - q["x"]) <= q["hw"]
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


def require_run_scope(root: Path, world: str | None = None,
                      seed: str | None = None) -> dict:
    """REFUSE a ledger run whose seq-0 manifest is not this run's world/seed.

    THE LEDGER DECLARES ITS OWN SCOPE AND NOBODY READ IT.  Seq 0 is the
    `world_manifest`, written for exactly this reason -- its own `meta.why`
    says "seq 0 pins the world and the environment; replay refuses on mismatch
    rather than substituting" -- and it carries `world`, `seed` (the numeric
    hash) and `seed_name`.  `replay.py` honours it; every OTHER reader took the
    run directory's NAME as the answer, and `--root` bypasses the name
    entirely.

    So the declaration is read here, by the one function, and compared against
    the run's resolved pair.  An empty or unreadable run is not refused: there
    is no declaration yet to disagree with, and `Ledger.open` writes seq 0 on
    first use.
    """
    path = Path(root) / "ledger.jsonl"
    if not path.is_file() or path.stat().st_size == 0:
        return {}
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    try:
        rec = json.loads(first)
    except ValueError:
        return {}
    if rec.get("op") != "world_manifest":
        return {}
    params = rec.get("params") or {}
    return worldseed.require_scope(
        "ledger run", path,
        found_seed=params.get("seed_name"), found_world=params.get("world"),
        seed_=seed, world_=world,
        produced_by="ledger/writer.py Ledger.open (seq 0 world_manifest)")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("op", choices=["spec", "fingerprint", "verify", "report"])
    worldseed.add_arguments(ap)
    ap.add_argument("--root")
    ap.add_argument("--light", action="store_true",
                    help="fingerprint without hashing the plugin DLLs")
    args = ap.parse_args()
    seed, world = worldseed.apply(args)

    if args.op == "spec":
        print(schema.describe())
        return 0
    if args.op == "fingerprint":
        print(json.dumps(fingerprint(world, heavy=not args.light),
                         indent=1)[:4000])
        return 0

    root = Path(args.root) if args.root else RUNS / world
    # BEFORE the chain is opened, and it matters most with `--root`: that flag
    # detaches the run from the directory name, so the name can no longer be
    # even a weak claim about which world these records belong to.
    require_run_scope(root, world, seed)
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
