#!/usr/bin/env python3
"""THE INSTRUMENTS - one authority per question, shared by every section.

Moved here VERBATIM out of `roads/accept.py`, where they were written and
where they were proved.  Nothing in this file is new: the comments are the
record of WHY each instrument is built the way it is, and every one of them
is a defect this project shipped.  If you are tempted to write a second
census, a second ground reader or a second rotate, read the comment on the
one that is already here first - a second implementation of a shared helper
has cost this project twice.

  GROUND    `run_waterproof.sh FROM_SAVE=1` on a COPY of the save; the save's
            own `_TerrainCompiler` ZDOs instantiated by the game; then
            `Heightmap.GetHeight`.  NEVER the ledger's heights, never the
            datum a write chose, and never `ribbon.py::walkability`, which
            printed WALKABLE on all sixteen broken segments both before and
            after they were re-graded.
  OBJECTS   `ZDOMan.m_objectsByID` read offline through `WATERPROOF_ZDOS` -
            491,792 ZDOs in one 51 s pass, with rotation.  No paging, no
            radius, no 4050-byte reply cap, no console saturation.
  WALKER    MEASURED off the Player prefab.  `census()` REFUSES TO RETURN if
            the harness will not give up the capsule, rather than
            substituting a guess.
  LIMIT     `tan(38 deg)` per 1 m lattice edge, from
            `Character::c_MinSlideDegreesPlayer`.  There is no step-height
            constant in Valheim; on terrain the slide angle is the entire
            limit.
  SNAPSHOT  the live save is frozen to one CONTENT-HASHED copy before the
            first batch, because the dedicated server rewrites the world
            every few minutes and batch 1 and batch 5 would otherwise be two
            different worlds joined by a single verdict.
  SELFCHK   ground batches OVERLAP by one station and the run ABORTS if the
            same station reads two heights.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent

# ---------------------------------------------------------------------------
# MEASURED CONSTANTS.  Each one names where it was read from.  Nothing on this
# list is a design choice, and the gate refuses to run if the harness fails to
# re-measure the two that come off the Player prefab.
# ---------------------------------------------------------------------------
SLIDE_DEG = 38.0
# Character::c_MinSlideDegreesPlayer, `.field private static literal float32
# c_MinSlideDegreesPlayer = float32(38.)` in assembly_valheim.dll 1.0.12.
STEP_LIMIT_PER_M = math.tan(math.radians(SLIDE_DEG))      # 0.78129 m per 1 m
SAMPLE_M = 1.0
ZONE_SIZE = 64.0

# THE SLOPE A DISPOSAL ACTUALLY SOLVES AT, WHICH IS NOT THE LIMIT.
#
# `STEP_LIMIT_PER_M` is what the WALKER is judged against.  No repair in this
# tree is written onto it: `roads/cone.py` builds its Lipschitz cone at
# 0.770 m per 1 m, and the reason is stated where it is used - a write solved
# ONTO a limit is a write whose legality depends on the last decimal of a
# float, and the gate re-reads the ground through a different path than the
# planner did.
#
# IT IS HERE BECAUSE A REFUSAL'S FOOTPRINT IS DERIVED FROM ITS DEPTH.
# `settlements.repair_ownership` sizes the ground a repair must touch as
# `depth / STEP_LIMIT_PER_M + SAMPLE_M * sqrt(2)`, and every step check was
# handing it `step - STEP_LIMIT_PER_M`: the depth that leaves the transition
# EXACTLY ON the limit, which is a depth no disposal will ever write.  The
# ownership question was therefore being asked about a repair that does not
# exist.
#
# MEASURED on Ulfsland 245daa7f, all 6 step-shaped refusals that name a 1 m
# edge, asked BOTH ways against the live flatten=FORBIDDEN extent set: ONE
# flips, and it is the whole reason this constant exists.
# roads/traversability T13-temple-harboursouth station 423, the 0.794 m step
# at (14,-255)->(14,-256):
#     depth step-LIMIT  = 0.0127 m -> footprint 1.430 m -> owner None
#                                     -> repairable True  -> ACTIONABLE
#     depth step-SLOPE  = 0.0240 m -> footprint 1.445 m
#                                     -> harbour_place seq 24 at 1.431 m
#                                     -> repairable False -> 1A_FORBIDDEN
# The owner is 1.4314 m away and the two footprints straddle it.  That row
# spent a full convergence wave on the work queue as "13 mm over" while
# `Ledger.append` would have refused every cut that closes it.  The other
# five do not move.
DISPOSAL_SLOPE_PER_M = 0.770


def disposal_depth(step_m: float, step_limit: float = STEP_LIMIT_PER_M
                   ) -> float:
    """How deep a cut-only disposal must actually go to close a `step_m`
    transition judged against `step_limit`.

    Scaled rather than subtracted so a fixture judging at its own limit gets
    a disposal slope in the same proportion, and so there is exactly one
    place in this tree that knows how far inside a limit a repair is solved.
    """
    slope = step_limit * (DISPOSAL_SLOPE_PER_M / STEP_LIMIT_PER_M)
    return max(float(step_m) - slope, 0.0)

# THE GROUND INSTRUMENT'S OWN QUANTUM, AND A LIMIT MAY NOT BE JUDGED FINER.
#
# `terraform/WaterProof.cs` prints the applied bed as `app.ToString("0.####")`
# - FOUR decimals - and that string is what `ground()` caches and returns.  So
# every reading is its float32 height rounded to 0.0001 m, and the DIFFERENCE
# of two readings is uncertain by up to one whole quantum.  `STEP_LIMIT_PER_M`
# is tan(38 deg) = 0.781285627, a number with more digits than the instrument
# has, and comparing the two directly decides the verdict on the rounding.
#
# MEASURED on drive 2's frozen Fresh42 save a521b70356e7.  terminus_step
# refused T3 B and S8 B at "0.781 m" against 0.7813, the readings being
#     (460,-107) 33.3601 -> (461,-107) 34.1414   = 0.7813
#     (-701,-283) 60.3902 -> (-700,-283) 61.1715 = 0.7813
# and 0.7813 > 0.7812856.  THE UNROUNDED LATTICE says otherwise: generated
# float32 heights from the writer's own zone patches (digest-verified against
# the write records' `generated_heights_sha256`) plus the live TCData
# levelDelta of seqs 1498 and 1761, summed in float32 as
# `TerrainComp::ApplyToHeightmap` does, give 33.360073 -> 34.141357 and
# 60.390186 -> 61.171470: BOTH 0.78128433, 1.29e-6 UNDER the limit.  Both are
# `roads/ribbon.py`'s batter, which is solved at `BATTER_GRADE = tan(38 deg)`,
# ON the limit.  Two false refusals, one printed rounding.
#
# So a step is over the limit only when it is over by MORE than the quantum a
# difference of two readings carries.  Inside that band this instrument cannot
# say which side of the limit the ground is on, and the callers COUNT such
# transitions beside the verdict rather than refusing on the rounding or
# passing them silently.
GROUND_QUANTUM_M = 1e-4
# The comparison as it shipped, restorable by a neuter: the limit judged to
# more decimals than the reading has.  False is the fix.
_STEP_JUDGED_FINER_THAN_THE_READING = False


def exceeds_step_limit(step_m: float, limit: float,
                       quantum: float = GROUND_QUANTUM_M) -> bool:
    """Is `step_m` - a difference of two ground readings - over `limit` by
    more than those readings can resolve?  See `GROUND_QUANTUM_M`."""
    if _STEP_JUDGED_FINER_THAN_THE_READING:
        return step_m > limit
    return step_m - limit > quantum


def within_step_resolution(step_m: float, limit: float,
                           quantum: float = GROUND_QUANTUM_M) -> bool:
    """Over the limit as read, but by no more than the reading's quantum:
    the instrument cannot say which side of the limit this ground is on."""
    return step_m > limit and not exceeds_step_limit(step_m, limit, quantum)

# ZoneSystem.m_waterLevel.  Named here so no section derives its own sea
# level: "is this wet" is one question and it gets one number.
WATER_LEVEL = 30.0

# The lateral geometry of a road comes from the road's OWN write record
# (`profile.half_width_m`, `profile.shoulder_m`), not from a constant here, so
# a world whose roads are a different width is measured at its own width.

# How far from a centreline an object is still loaded into the model.  Not an
# acceptance number: it only bounds the WORK.  24 m is three carriageways
# either side, so a body that reaches into the road is taken whole and its
# piece count is the body's, not a clipped one.
CORRIDOR_MASK_M = 24.0

WORLD_DEFAULT = "Ulfsland"
VH_SRC_DEFAULT = "/media/big4/projects/game/valheim/Ulfsland/data/bepinex"
# A WORLD'S SAVE LIVES UNDER ITS OWN NAME: `<root>/<World>/config_merged/
# worlds_local/<World>`, the layout `pipeline.Scope.world_src` declares.  It
# used to be a constant here, `WORLD_SRC_DEFAULT`, naming Ulfsland's save, and
# six readers took it for EVERY world -- `roads/ribbon.py`'s regrade census
# fingerprinted and censused Ulfsland's live save on whatever world it was
# building.  The root is `worldseed.world_root()`, the ONE resolver
# (`$VALHEIM_WORLD_ROOT`, else `$VALHEIM_ROOT`, else the production install),
# asked at CALL time.  It was a module constant reading `$VALHEIM_WORLD_ROOT`
# alone while the ledger's manifest read `$VALHEIM_ROOT`, so a run whose
# operator environment named its install through `$VALHEIM_ROOT` -- the
# from-scratch Ulfsland regeneration, root `UlfStaging` -- had every one of
# this module's readers freeze and census the PRODUCTION save of the same
# name.  See `worldseed.world_root` for the measurement.


def world_src_default(world: str | None = None, *,
                      root: str | None = None) -> str:
    """The save directory of `world`, default this run's world, at CALL time."""
    w = str(world or _worldseed().world())
    return f"{root or _worldseed().world_root()}/{w}/config_merged/worlds_local/{w}"


# WORLD_SAVE_DEPENDS_ON = ("world_being_built", "save_declares_it").  Two
# neuters, one per direction, registered in `worldseed.FIXED_NAME_NEUTERS`:
#   literal    the shipped line -- ONE world's save for every world, unchecked
#   unchecked  keyed on the world, but a save that is not there is not refused
_WORLD_SAVE_MODE = "real"       # real | literal | unchecked


def world_save(world: str | None = None, *, root: str | None = None) -> Path:
    """The save of the world BEING BUILT, refused unless it is that world's.

    `$JUMPSTART_WORLD_SRC` names an install explicitly -- the variable
    `settlements/build.py::_world_src` already reads -- else the layout
    default under `root`.  Either way `require_world_save` reads the world's
    name off the directory, so a save that cannot be resolved, or resolves to
    another world's, stops the caller instead of being measured.
    """
    w = str(world or _worldseed().world())
    if _WORLD_SAVE_MODE == "literal":
        return Path(world_src_default(_worldseed().DEFAULT_WORLD, root=root))
    src = Path(os.environ.get("JUMPSTART_WORLD_SRC")
               or world_src_default(w, root=root))
    if _WORLD_SAVE_MODE != "unchecked":
        require_world_save(src, w)
    return src



SANDBOX_DEFAULT = "/tmp/waterproof/vh"
CACHE_DEFAULT = "/tmp/accept"


# ---------------------------------------------------------------------------
# the save, and a fingerprint of it
# ---------------------------------------------------------------------------
def save_world_name(world_src: Path) -> str | None:
    """The world THE SAVE SAYS IT IS, from the save's own layout on disk.

    Valheim 1.0.12 stores a save as the DIRECTORY `worlds_local/<World>/`
    holding `_main.<n>.fwl2` and friends -- the layout `valheim_worldgen.py`
    detects rather than assumes, for the same reason it is read here rather
    than assumed.  The 0.220.x pair layout put the name in the stem of
    `<World>.fwl`, so both are read; a directory that holds neither is not a
    save and returns None rather than a guess.
    """
    if world_src.is_dir():
        if any(p.name.endswith(".fwl2") for p in world_src.iterdir()):
            return world_src.name
        fwl = [p for p in world_src.iterdir() if p.suffix == ".fwl"]
        if len(fwl) == 1:
            return fwl[0].stem
    elif world_src.suffix in (".fwl", ".fwl2"):
        return world_src.stem.split(".")[0]
    return None


def require_world_save(world_src: Path, world: str) -> str:
    """REFUSE a save that is not the world this run says it is measuring.

    See the note at `accept/__main__.py` Ctx.__init__ for the measurement this
    closes.  Fails CLOSED: a gate that reports one world's verdict over
    another world's ground is worse than a gate that will not start, because
    its output reads as evidence.
    """
    if not world_src.exists():
        raise SystemExit(
            f"REFUSING: --world-src {world_src} does not exist, so there is "
            f"no ground to measure world {world!r} against. Point it at that "
            f"world's worlds_local/<World> directory.")
    found = save_world_name(world_src)
    if found is None:
        raise SystemExit(
            f"REFUSING: {world_src} holds no .fwl2 or .fwl, so it cannot be "
            f"shown to be world {world!r}'s save. A directory that does not "
            f"name its own world is how one world's ground ends up under "
            f"another world's verdict.")
    if found.lower() != world.lower():
        raise SystemExit(
            f"REFUSING: --world is {world!r} but --world-src {world_src} is "
            f"world {found!r}'s save. The verdict would be the JOIN OF TWO "
            f"WORLDS -- {world!r}'s ledger over {found!r}'s ground -- and "
            f"nothing downstream could tell. Pass --world-src for {world!r}, "
            f"or --world {found!r} if {found!r} is what you meant to measure.")
    return found


# THE SAVE MUST BE THE ONE THE LEDGER IS ABOUT, NOT MERELY ONE OF THE SAME NAME.
#
# `require_world_save` reads the world's name off the directory, and two
# worlds on one host can carry one name: the from-scratch Ulfsland
# regeneration (install root `UlfStaging`, world uid 1008370488) and the live
# Ulfsland (uid 2212643513) are both `Ulfsland` on seed Pirate68.  MEASURED
# 2026-10-05: ribbon.py's regrade census passed the name check on the live
# world's save and refused 19 road legs for burying pieces that exist only
# there.  The ledger's seq-0 `world_manifest` pins which world it is about, so
# the save is compared with THAT.
#
#   world_uid     the world's own identity (`ledger/writer.read_fwl`), stable
#                 for the world's life.  Decisive both ways when pinned.
#   fwl_sha256    pinned by every manifest, but the file's BYTES are not
#                 stable for the world's life: Ulfsland's live `.fwl2` is
#                 8833746a... today and its ledger pins c5fdbf8f..., the same
#                 world.  Equal proves identity; different proves nothing, so
#                 a manifest that pins only the hash is reported UNVERIFIABLE
#                 when they differ and never refused on it.
#
# A world with no ledger yet has nothing to be compared with (status
# `unbuilt`), exactly as `accept/__main__.py` already treats it.
_SAVE_IDENTITY_MODE = "real"       # real | off (the shipped, name-only check)


def _ledger_writer():
    """`ledger/writer.py`, the one reader of a `.fwl2` header."""
    led = str(HERE.parent / "ledger")
    if led not in sys.path:
        sys.path.append(led)
    import writer                                          # noqa: PLC0415
    return writer


def ledger_pin(ledger: Path) -> tuple[str, dict]:
    """(status, pins) from the ledger's seq-0 manifest.  `unbuilt` when there is
    no ledger yet; `unpinned` when its manifest pins neither uid nor hash;
    `pinned` otherwise, with `pins` holding `world_uid` and/or `fwl_sha256`."""
    ledger = Path(ledger)
    if not ledger.is_file() or ledger.stat().st_size == 0:
        return "unbuilt", {}
    with ledger.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    try:
        rec = json.loads(first)
    except ValueError as exc:
        raise SystemExit(f"REFUSING: {ledger}'s first line is not JSON ({exc}), "
                         f"so it cannot show which world it is about.")
    if rec.get("op") != "world_manifest":
        raise SystemExit(f"REFUSING: {ledger}'s first record is "
                         f"{rec.get('op')!r}, not the world_manifest that "
                         f"pins which world the ledger is about.")
    params = rec.get("params") or {}
    pins = {k: params[k] for k in ("world_uid", "fwl_sha256")
            if params.get(k) is not None}
    return ("pinned" if pins else "unpinned"), pins


def save_identity(world_src: Path) -> dict:
    """`{world_uid, fwl_sha256}` of the newest `.fwl2` in a save directory, via
    the reader the ledger's manifest was written with; `{}` for a directory
    that holds none or whose header does not parse.  No identity is claimed
    then: a ledger that pins the uid refuses such a save, and one that pins
    only the hash reports it unverifiable."""
    import struct                                          # noqa: PLC0415
    fwl = _worldseed().newest_save(Path(world_src), "fwl2")
    if fwl is None:
        return {}
    try:
        got = _ledger_writer().read_fwl(Path(fwl))
    except (struct.error, IndexError, UnicodeDecodeError):
        return {}
    return {"world_uid": got["uid"], "fwl_sha256": got["fwl_sha256"]}


def require_save_is_the_ledgers(world_src: Path, world: str,
                                ledger: Path | None = None) -> str:
    """REFUSE a save that is not the world `ledger` is about.  Returns the
    status: `same`, `unbuilt`, `unpinned`, `unverifiable` or `unchecked`."""
    if _SAVE_IDENTITY_MODE == "off":
        return "unchecked"
    ledger = Path(ledger) if ledger else (JUMPSTART / "ledger" / "runs"
                                          / str(world) / "ledger.jsonl")
    status, pins = ledger_pin(ledger)
    if status != "pinned":
        return status
    got = save_identity(world_src)
    if "world_uid" in pins:
        if got.get("world_uid") != pins["world_uid"]:
            raise SystemExit(
                f"REFUSING: {world_src} is world uid {got.get('world_uid')}, "
                f"but {ledger} is about world uid {pins['world_uid']}. Both "
                f"may be called {world!r}; the verdict would be one world's "
                f"ledger over another world's ground. Point the run at the "
                f"install it is building ($VALHEIM_ROOT / --world-root / "
                f"$JUMPSTART_WORLD_SRC).")
        return "same"
    if got.get("fwl_sha256") == pins["fwl_sha256"]:
        return "same"
    return "unverifiable"


def save_fingerprint(world_src: Path) -> str:
    """Identity of the SAVE, by CONTENT, so a cached reading cannot outlive
    its world and cannot be invalidated by a mere touch.

    Content and not mtime, because the live server rewrites the save every
    few minutes: an mtime hash would make every run a cache miss and, worse,
    would let two batches of one run measure two different worlds.
    """
    h = hashlib.sha256()
    for p in sorted(world_src.iterdir()):
        if not p.is_file():
            continue
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def snapshot(world_src: Path, dest: Path) -> Path:
    """One frozen copy of the world, measured by every instrument in the run.

    THE LIVE SAVE MOVES UNDER THE INSTRUMENT.  A road network is several
    hundred zones and has to be read in batches; the dedicated server writes
    its world every few minutes; so batch 1 and batch 5 would otherwise be
    readings of two different worlds joined by a single verdict.  The
    harness already copies whatever it is pointed at, but pointing it at the
    LIVE directory five times is five copies of five different moments.
    """
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(world_src, tmp)
    tmp.rename(dest)
    return dest


# ---------------------------------------------------------------------------
# THE TERRAIN EPOCH - the state the GROUND instrument is actually a function
# of, and therefore the name its cache directory should have carried
# ---------------------------------------------------------------------------
# `ground()` returns the engine's bed height at a 1 m lattice vertex.  THAT
# NUMBER IS A FUNCTION OF THE TERRAIN AND OF NOTHING ELSE - the seed, whether
# the zone is generated, and the `_TerrainCompiler` deltas covering the
# vertex.  It was nevertheless filed under `save_fingerprint()`, the hash of
# the WHOLE SAVE, so every build-only write - a bridge deck, a sign, one of
# the dedicated server's own autosaves - threw away a reading that could not
# have changed.
#
# MEASURED 2026-09-23 against this world's own cache, /tmp/accept/Ulfsland,
# by intersecting the point indexes of fingerprints taken either side of a
# known ledger event.  81 fingerprints, 5.2 GB on disk:
#
#   a087af62 (save seq 5416) vs 0571968d (the server's own 03:15 autosave,
#     with NO ledger record at all between the two):
#         421,634 shared vertices, 0 disagreements, max delta 0.0000 m
#   245daa7f (save seq 5420) vs 7632b3a5 (save seq 5428, seven build-only
#     spawn_plan records later):
#           1,919 shared vertices, 0 disagreements, max delta 0.0000 m
#   a087af62 (seq 5416) vs 245daa7f (seq 5420), terrain_write 5417 and 5419
#     in between:
#         451,702 shared vertices, 11 DISAGREEMENTS, max delta 0.2700 m
#
# Both directions are real, and the separator is the ledger: the third pair
# has a terrain write between its ends and the other two do not.  Note the
# SHAPE of the third number - 11 in 451,702.  A stale hit is not loud; it is
# eleven wrong heights hiding in half a million right ones, which is why the
# guard below is not a uniform random sample (see `ground_guard`).
#
# WHY THE PREDICATE IS "WHAT WENT DOWN THE WIRE", NOT "WHAT THE OP IS
# CALLED".  An `op` is a label its writer chose; `wire` is the list of
# console commands the game actually received.  A bed can only move if one
# of those commands creates, deletes or alters a `_TerrainCompiler` or
# regenerates a zone.  Classifying the wire means a record whose op is new,
# renamed or simply wrong is still classified by WHAT IT DID.
#
# AND IT FAILS CLOSED.  An unrecognised verb, a `deleteObjects` with no
# `-prefab`, an `objects_remove` whose wildcard is not fenced off the
# underscore prefabs, a `modifyObject` whose record does not declare its
# prefab: every one of them counts as TERRAIN-MOVING.  A false "it moved"
# costs one cold read.  A false "it did not" is a silent wrong height under
# every downstream number.
#
# COMPLETENESS, STATED PLAINLY RATHER THAN CLAIMED.  This is NOT a proof
# that nothing else can move a bed.  It is (a) a proof that nothing can move
# a bed WITHOUT SENDING A COMMAND, and (b) a fail-closed classification of
# commands, so the list of terrain-moving OPS never has to be complete - the
# unknown op is terrain-moving by default.  MEASURED over Ulfsland's 5,433
# records: 796 sent nothing at all (note/observe/world_manifest/
# chain_reset), and every one of the remaining 4,637 carried a non-empty
# wire, so the "sent nothing" arm is decided by the field and not by a name.
#
# WHAT IS *NOT* COVERED, AND WHO CATCHES IT: a terrain edit made outside the
# ledger - a player with a hoe, an unrecorded UpgradeWorld run - leaves this
# key unchanged.  No ledger-derived key can see it.  It is caught by
# `ground()`'s existing cross-reading abort, which this change makes
# STRONGER, not weaker: today two saves' readings of one vertex live in two
# different fingerprint directories and can never meet, so they can never
# disagree out loud.  Sharing one directory is what makes them meet.  The 11
# disagreements above are precisely what that abort would have named.
_EPOCH_FORMAT = 1

# The console verbs that provably cannot move a bed: `save` serialises the
# world, `start` restarts the container.  Neither reaches a compiler.
_BED_SAFE_VERBS = ("save", "start")


@dataclass
class TerrainEpoch:
    """WHICH TERRAIN a ground reading belongs to, plus the save it came off.

    `key` names the cache directory.  Everything else is evidence for the
    refusals: a gate that says "cache miss" without being able to say WHICH
    record moved the terrain is a gate nobody can act on.
    """

    key: str
    head_seq: int              # highest ledger seq this key was computed at
    last_move_seq: int         # the last record that could move a bed
    moves: int                 # records in the whole ledger that could
    records: int               # records classified
    by_op: dict = field(default_factory=dict)
    save_fp: str = ""          # the save this run is reading
    save_generation: int = -1  # N in _main.N.fwl2, which ORDERS two saves
    ledger: str = ""


def save_generation_of(world_src: Path) -> int:
    """`N` in `_main.N.fwl2` - `SaveSystem::GetSaveNumber()`, bumped on every
    save, so it ORDERS two saves of one world where a content hash cannot.
    -1 when the save carries none, which is a refusal input and not a zero.
    """
    best = -1
    if world_src is not None and Path(world_src).is_dir():
        for p in Path(world_src).iterdir():
            if p.name.startswith("_main.") and p.name.endswith(".fwl2"):
                try:
                    best = max(best, int(p.name.split(".")[1]))
                except (IndexError, ValueError):
                    continue
    return best


def wire_moves_bed(line) -> str:
    """"" if this console command provably cannot change a bed height, else
    the reason it can - in words a refusal can quote back."""
    if not isinstance(line, str):
        return f"wire entry is a {type(line).__name__}, not a command"
    toks = line.split()
    if not toks:
        return "empty wire line"
    verb = toks[0]
    if verb in _BED_SAFE_VERBS:
        return ""
    if verb == "spawn_object":
        if len(toks) < 2:
            return "spawn_object names no prefab"
        return "" if not toks[1].startswith("_") else f"spawns {toks[1]}"
    if verb == "objects_remove":
        kv = dict(t.split("=", 1) for t in toks[1:] if "=" in t)
        ids = kv.get("id", "")
        ignore = [s for s in kv.get("ignore", "").split(",") if s]
        named = [s for s in ids.split(",") if s]
        if not named:
            return "objects_remove names no id set"
        if any(s.startswith("_") for s in named):
            return f"removes id={ids}"
        if any("*" in s for s in named) and "_*" not in ignore:
            return (f"id={ids} can match a _TerrainCompiler and ignore="
                    f"{kv.get('ignore') or '(none)'} does not fence it off")
        return ""
    if verb == "deleteObjects":
        names = [toks[i + 1] for i, t in enumerate(toks)
                 if t == "-prefab" and i + 1 < len(toks)]
        if not names:
            return "deleteObjects is not scoped to a -prefab"
        bad = [n for n in names if n.startswith("_")]
        return f"deletes {','.join(bad)}" if bad else ""
    return f"unrecognised console verb {verb!r}"


def record_moves_bed(rec: dict) -> str:
    """"" if this ledger record provably cannot change a bed height."""
    if not isinstance(rec, dict):
        return "ledger record is not an object"
    wire = rec.get("wire") or []
    if not wire:
        return ""
    params = rec.get("params") or {}
    for line in wire:
        toks = str(line).split()
        if toks and toks[0] == "modifyObject":
            # `modifyObject 9779:1 -rotation 0 0 0` names a ZDO ID, not a
            # prefab, so the wire alone cannot say WHAT was altered and the
            # record's own declaration is the only evidence there is.  An
            # absent declaration is a refusal, not a pass.
            pf = params.get("prefab")
            if not isinstance(pf, str) or not pf:
                return "modifyObject on a record that declares no prefab"
            if pf.startswith("_"):
                return f"modifyObject on {pf}"
            continue
        why = wire_moves_bed(line)
        if why:
            return why
    return ""


def ledger_records(ledger: Path) -> list:
    """Every record, in file order.  A line that will not parse is NOT
    skipped quietly: it comes back as a marker that classifies as
    terrain-moving, so an unreadable ledger can never look like an
    unchanged one."""
    ledger = Path(ledger)
    if not ledger.exists():
        raise RuntimeError(
            f"no ledger at {ledger}: the ground cache is keyed on the terrain "
            "history and there is none to read.  A missing ledger is a FAILED "
            "READ, not a world where nothing has happened.")
    out = []
    for n, line in enumerate(
            ledger.read_text(errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        if not isinstance(rec, dict):
            rec = {"op": "UNPARSEABLE", "seq": -n,
                   "wire": [f"unparseable ledger line {n}"]}
        out.append(rec)
    return out


def terrain_epoch(ledger: Path, save_fp: str = "",
                  world_src=None) -> TerrainEpoch:
    """The ground cache's directory name: a digest of every ledger record
    that could have moved a bed, in order.

    A DIGEST and not "the seq of the last terrain_write", for two reasons.
    A bare seq cannot tell an amended history from an unchanged one - this
    ledger has a `chain_reset` in it, so amendment is not hypothetical.  And
    a digest over the classified SET makes the key self-invalidating: the
    day somebody reclassifies a verb, the set changes and every affected
    epoch gets a new directory without anyone remembering to bump a version.
    """
    h = hashlib.sha256(f"groundkey/v{_EPOCH_FORMAT};".encode())
    moves = last = 0
    by_op: dict[str, int] = {}
    recs = ledger_records(ledger)
    for rec in recs:
        why = record_moves_bed(rec)
        if not why:
            continue
        moves += 1
        seq = rec.get("seq")
        seq = seq if isinstance(seq, int) else -1
        last = max(last, seq)
        op = str(rec.get("op"))
        by_op[op] = by_op.get(op, 0) + 1
        h.update(json.dumps({"seq": seq, "op": op, "wire": rec.get("wire")},
                            sort_keys=True, default=str).encode())
        h.update(b"\x00")
    head = max((r["seq"] for r in recs if isinstance(r.get("seq"), int)),
               default=-1)
    return TerrainEpoch(
        key=h.hexdigest()[:24], head_seq=head, last_move_seq=last,
        moves=moves, records=len(recs), by_op=by_op, save_fp=save_fp,
        save_generation=save_generation_of(world_src), ledger=str(ledger))

# ---------------------------------------------------------------------------
# INSTRUMENT 1 - the object census, read out of the save offline
# ---------------------------------------------------------------------------
def _stable_hash(s: str) -> int:
    """Valheim's `StringExtensionMethods.GetStableHashCode`.

    Needed because a mod-free sandbox's `ZNetScene` cannot name a modded
    prefab, so the census emits those rows with an empty name and they are
    recovered here by hashing every prefab-shaped token this repo knows.
    """
    def s32(v: int) -> int:
        v &= 0xFFFFFFFF
        return v - (1 << 32) if v >= (1 << 31) else v
    n = m = 5381
    i, ln = 0, len(s)
    while i < ln:
        n = (((n << 5) + n) & 0xFFFFFFFF) ^ ord(s[i])
        if i == ln - 1:
            break
        m = (((m << 5) + m) & 0xFFFFFFFF) ^ ord(s[i + 1])
        i += 2
    return s32(s32(n) + s32(m) * 1566083941)


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{2,63}")
_NAME_SOURCES = ("blueprints/data", "roads", "crossings", "library",
                 "settlements", "network", "ledger/runs", "terraform")


def modded_name_table(cache: Path) -> dict[int, str]:
    """hash -> prefab name for every prefab-shaped token in this repo's data.

    A sweep rather than a curated list, because the point is to name whatever a
    mod put in the world; a curated list would go stale on the next mod update
    and the census would silently report an unnamed hash as "unknown" forever.
    """
    cf = cache / "hash_names.json"
    if cf.exists():
        return {int(k): v for k, v in json.loads(cf.read_text()).items()}
    seen: set[str] = set()
    for root in _NAME_SOURCES:
        base = JUMPSTART / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in (".json", ".jsonl", ".yaml",
                                                   ".tsv", ".txt"):
                continue
            try:
                seen.update(_TOKEN_RE.findall(p.read_text(errors="ignore")))
            except OSError:
                continue
    table = {_stable_hash(t): t for t in seen}
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps({str(k): v for k, v in table.items()}))
    return table


# The harness is a SINGLETON and the reason is a hard-coded port.
# `run_waterproof.sh` starts a dedicated server on a fixed port; a second
# concurrent run loses the bind, `GameServer.Init()` fails, the plugin phase
# never runs, and the process exits 0 with no output file.  MEASURED by
# GateStructures: "CreateBoundSocket: ::bind couldn't find an open port
# between 3460 and 3460", raw_exit 0.  A separate SANDBOX does not help -
# the port is global to the machine.
#
# So: one lock for every caller in every process, and a precheck that names
# the port.  "harness produced no output" for a condition that is really
# "somebody else has the port" is the same fault as reading "No objects
# found matching the provided criteria" as a protocol error - a true message
# about the wrong thing.
# run_waterproof.sh launches on WATERPROOF_PORT (default 3459) and Steam
# binds the next one up, so a candidate needs BOTH free.
HARNESS_PORT_BASE = 3459
HARNESS_LOCK = Path("/tmp/accept/.harness.lock")


def _port_busy(port: int) -> bool:
    import socket                                         # noqa: PLC0415
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


@contextlib.contextmanager
def harness_lock(what: str):
    """Serialise every server-spawning read, and pick a port nobody holds.

    TWO SEPARATE FAULTS, BOTH FIXED HERE rather than worked around by taking
    turns.  The LOCK stops two callers in this package overlapping.  The
    PORT SEARCH stops a run this package did not start - somebody's shell,
    another agent, Steam itself - from turning our read into a silent empty
    one: the server loses the bind, `GameServer.Init()` fails, the plugin
    phase never runs, and the process exits 0 with no output file.

    Yields the base port to launch on.  `WATERPROOF_PORT` / `-port` is
    plumbed through `run_waterproof.sh`, so a future machine can run two.
    """
    import fcntl                                          # noqa: PLC0415
    HARNESS_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with HARNESS_LOCK.open("a+") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lk.seek(0)
            who = (lk.read().strip().splitlines() or ["another run"])[-1]
            sys.stderr.write(f"[accept] waiting for the harness lock, held "
                             f"by {who}\n")
            fcntl.flock(lk, fcntl.LOCK_EX)
        t0 = time.time()
        port = None
        while port is None:
            for base in range(HARNESS_PORT_BASE, HARNESS_PORT_BASE + 20, 2):
                # The server binds `base`; Steam binds `base + 1`.  Both must
                # be free or the bind fails on the one nobody checked.
                if not _port_busy(base) and not _port_busy(base + 1):
                    port = base
                    break
            if port is None:
                if time.time() - t0 > 1800:
                    raise RuntimeError(
                        f"every harness port from {HARNESS_PORT_BASE} to "
                        f"{HARNESS_PORT_BASE + 19} has been held for 30 "
                        "minutes by runs this process did not start; "
                        "refusing to launch a server that would lose the "
                        "bind and return an empty reading with exit code 0")
                sys.stderr.write("[accept] every harness port is in use by "
                                 "another run; waiting\n")
                time.sleep(5.0)
        lk.seek(0)
        lk.truncate()
        lk.write(f"pid {os.getpid()} port {port} "
                 f"{time.strftime('%H:%M:%S')} {what}\n")
        lk.flush()
        yield port


def _rows_in(path: Path) -> int:
    """Data rows in a harness artefact.  A ZERO-ROW file is a FAILED READ.

    "declared 308 objects, returned 41" and "no output" for a lost port are
    the same fault: an instrument that cannot tell `nothing to see` from
    `I never looked`.  An empty artefact is never returned to a caller.
    """
    n = 0
    with path.open(errors="ignore") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                n += 1
    return n


def run_harness(env_extra: dict, out: Path, vh_src: str, world_src: str,
                world: str, sandbox: str, timeout: int = 5400,
                recheck: Path | None = None,
                skip_if=None) -> str:
    """Spawn one harness read, under the lock, on a port nobody holds.

    TWO CHECKS RUN INSIDE THE LOCK, because the caller's own checks happened
    outside it and check-then-act across a lock boundary is a race:

    `recheck` is the artefact the caller decided was missing.  Two agents
    running the same section at the same batch size both see the same file
    missing, both queue, and the lock then serialises them into TWO
    consecutive full-cost reads of identical points.  MEASURED tonight: two
    roads runs raced the same six batch files.

    `skip_if` is the stronger form and it is what the tag test could not do.
    The artefact name is a hash of the batch's own point list, so it only
    catches two runs asking the IDENTICAL question - two runs whose point
    sets merely OVERLAP are invisible to it.  MEASURED tonight as well: one
    run froze a 14,584-point corner list while another was mid-way through
    reading a 14,804-point superset of it, and the tag matched nothing.
    `skip_if()` is called once the lock is ours and answers "has somebody
    else already read every point I was going to read"; if it says yes, no
    server starts.  (RoadsRemeasure's diagnosis; this subsumes the tag test.)
    """
    env = dict(os.environ)
    env.update(VH_SRC=vh_src, WORLD_SRC=world_src, WORLD=world,
               SANDBOX=sandbox, OUT=str(out), POINTS="", SHIPS="", SETTLE="1",
               BLOBS="", REF="", POINTS_FILE="", ZONES="", ZDOS="",
               FROM_SAVE="1")
    env.update({k: str(v) for k, v in env_extra.items()})
    out.parent.mkdir(parents=True, exist_ok=True)
    with harness_lock(f"waterproof -> {out}") as port:
        tgt = recheck if recheck is not None else out
        if tgt.exists() and _rows_in(tgt) > 0:
            sys.stderr.write(
                f"[accept] {tgt.name} was produced by another run while we "
                "queued; consuming theirs rather than reading it twice\n")
            return ""
        if skip_if is not None and skip_if():
            sys.stderr.write(
                "[accept] every point of this batch was read by another run "
                "while we queued; serving them from the shared index rather "
                "than starting a server\n")
            return ""
        env["PORT"] = str(port)
        cp = subprocess.run(
            ["bash", str(JUMPSTART / "terraform" / "run_waterproof.sh")],
            env=env, capture_output=True, text=True, timeout=timeout)
    if not out.exists():
        raise RuntimeError(
            f"harness produced no output at {out}: the run exited without "
            f"writing its artefact (port {port}).  This is a FAILED READ, "
            "not an empty world.\n" + cp.stderr[-3000:])
    if _rows_in(out) == 0:
        raise RuntimeError(
            f"harness wrote {out} with ZERO rows: a zero-row artefact is "
            "indistinguishable from a world with nothing in it, so it is "
            "refused rather than cached.\n" + cp.stderr[-3000:])
    # EMPTY_POPULATION_DEPENDS_ON: WHETHER THE HARNESS RECORDED THE PASS.
    # Not on whether the artefact has rows.
    #
    # A zero-row `out` above IS a failed read -- the meta file is the
    # harness's own report and a harness that reports nothing did not run.
    # The SECONDARY artefacts are different, and treating them the same way
    # is the "absence reads as a fault" half of this project's recurring
    # defect.  MEASURED 2026-09-23 by FreshDrive on the fresh seed Fresh42:
    # the world holds 81 ZDOs and NO portals and NO guideposts, so the field
    # census is correctly empty -- and this loop raised "harness wrote
    # zdostr.tsv with ZERO rows", which the portals section reported as a
    # broken instrument on a world whose true answer is "there are none".
    #
    # The harness states which passes it ran, in `out`: `WriteZdoCensus`
    # appends `zdo_census <path> <n>` (WaterProof.cs:220) and
    # `WriteZdoFields` appends `zdo_fields <path> <rows>` (WaterProof.cs:942),
    # both AFTER the file is written.  So the line's presence is the
    # evidence that the pass happened and the count is its population --
    # which makes "0 rows and the pass ran" an EMPTY POPULATION and "0 rows
    # and no such line" a FAILED READ, and keeps the two apart.
    ran = {ln.split("\t")[1] for ln in out.read_text(
        encoding="utf-8", errors="ignore").splitlines()
        if ln.startswith(("zdo_census\t", "zdo_fields\t"))
        and len(ln.split("\t")) >= 3}
    for extra in (env_extra.get("ZDOS"), env_extra.get("ZDO_FIELDS")):
        if not extra or not Path(extra).exists():
            continue
        if _rows_in(Path(extra)) == 0 and str(extra) not in ran:
            raise RuntimeError(
                f"harness wrote {extra} with ZERO rows AND did not report "
                f"the pass in {out}: the reading did not happen, which is a "
                f"FAILED READ and not an empty population. Passes reported: "
                f"{sorted(ran) or 'none'}\n" + cp.stderr[-3000:])
    return cp.stderr


class Census:
    """Every ZDO the save holds, with its prefab name and world rotation."""

    def __init__(self, rows: list[tuple], walker_radius: float,
                 walker_height: float, unresolved: int):
        self.rows = rows            # (name, x, y, z, qx, qy, qz, qw)
        self.walker_radius = walker_radius
        self.walker_height = walker_height
        self.unresolved = unresolved


# ZDO_CENSUS_DEPENDS_ON: THE SAVE, AND NOTHING ELSE.
#
# Named and separated because the reachability of this reading was keyed on
# something far larger than the reading.  `accept/__main__.py` returns exit 2
# --  "<World> HAS NO BUILD LEDGER ... an empty world is not an accepted
# world" -- BEFORE any section runs, which is CORRECT FOR A VERDICT and
# wrong for a MEASUREMENT.  `Ctx.__init__` needs no ledger to reach here:
# this function's whole input is `vh_src`, `world_src`, `world`, `sandbox`.
#
# MEASURED 2026-09-23 on the fresh seed Fresh42: `roads/precensus.py
# from_save` wants exactly this artefact, `accept --section roads --world
# Fresh42 --world-src <Fresh42 save>` exited 2 in 0.35 s without ever
# calling it, and the remedy the gate printed -- "build something first" --
# is stage 12, which requires the census this file was being asked for.  A
# GUARD THAT CAN ONLY SPEAK AFTER THE BUILD IS A GUARD WHOSE REMEDY THE
# BUILD HAS ALREADY FORBIDDEN.  Every fresh seed was deadlocked on a key
# that named "can the gate reach a verdict" for a quantity that depends only
# on which bytes are in the save.
#
# So the acquisition is its own function, with its own declared dependency,
# and the two callers that want it -- the gate's section instruments and
# `accept --zdo-census`, which runs before the ledger check -- share this
# one home rather than each building the harness env.
ZDO_CENSUS_DEPENDS_ON = ("world_src", "world", "vh_src", "sandbox")


def zdo_census_tsv(cache: Path, fp: str, args) -> Path:
    """The whole-save ZDO dump for this save, ACQUIRED IF ABSENT.

    Returns the TSV path.  `zdos.meta.tsv` beside it carries the walker
    capsule; both are written by one harness run, so they are acquired
    together and their joint absence is the only trigger.
    """
    tsv = cache / fp / "zdos.tsv"
    meta = cache / fp / "zdos.meta.tsv"
    if not (tsv.exists() and meta.exists()):
        run_harness({"ZONES": "0,0", "ZDOS": str(tsv)}, meta,
                    args.vh_src, args.world_src, args.world, args.sandbox)
    return tsv


def census(cache: Path, fp: str, args) -> Census:
    tsv = zdo_census_tsv(cache, fp, args)
    meta = cache / fp / "zdos.meta.tsv"
    names = modded_name_table(cache)
    rows, unresolved = [], 0
    with tsv.open() as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 9:
                continue
            nm = f[1]
            if not nm:
                nm = names.get(int(f[0]), "")
                if not nm:
                    nm = "hash:" + f[0]
                    unresolved += 1
            rows.append((nm, float(f[2]), float(f[3]), float(f[4]),
                         float(f[5]), float(f[6]), float(f[7]), float(f[8])))
    rad = hgt = float("nan")
    for line in meta.read_text().splitlines():
        f = line.split("\t")
        if f[0] == "walker" and len(f) >= 7:
            rad, hgt = float(f[4]), float(f[6])
    if not (rad > 0 and hgt > 0):
        raise RuntimeError("the harness did not measure the Player capsule; "
                           "refusing to substitute a guessed walker width")
    return Census(rows, rad, hgt, unresolved)


# ---------------------------------------------------------------------------
# INSTRUMENT 1b - ZDO STRINGS, the fields a position cannot answer
# ---------------------------------------------------------------------------
# A portal pairs by EXACT STRING EQUALITY on its `tag`, and a guidepost binds
# to the arch it NAMES by its `text`.  Neither is a geometric fact, and
# binding a board to the NEAREST arch instead of the one it names is exactly
# how 16 of 17 boards were once certified while pointing at the wrong
# destination.  The rows come off the same whole-save ZDO pass.
class Strings:
    """(prefab, key) -> [(x, y, z, value)], plus the POPULATION of each key.

    A row with an EMPTY value is a measurement - that portal has a blank tag,
    which is the defect that matters.  An ABSENT row is a hole in the
    instrument.  The two are different and this class keeps them different.
    """

    def __init__(self, rows: list[tuple[str, float, float, float, str, str]]):
        self.by_key: dict[tuple[str, str], list] = defaultdict(list)
        for prefab, x, y, z, key, val in rows:
            self.by_key[(prefab, key)].append((x, y, z, val))
        self.available = bool(rows)
        # What the HARNESS declared it found, straight off its own meta file.
        # Kept separate from what this reader parsed, because "declared 308,
        # returned 41" is a real failure mode of this project's instruments
        # and `population()` compares the two rather than trusting either.
        self.declared: dict[tuple[str, str], dict] = {}

    def at(self, x: float, y: float, z: float, prefab: str, key: str,
           tol: float = 0.25) -> str | None:
        best, bd = None, tol * tol
        for px, py, pz, val in self.by_key.get((prefab, key), ()):
            d = (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2
            if d <= bd:
                best, bd = val, d
        return best

    def population(self, prefab: str, key: str) -> dict:
        rows = self.by_key.get((prefab, key), [])
        with_value = sum(1 for *_p, v in rows if v.strip())
        out = dict(rows=len(rows), with_value=with_value,
                   blank=len(rows) - with_value)
        d = self.declared.get((prefab, key))
        if d:
            out["declared_zdos"] = d["zdos"]
            out["declared_with_value"] = d["with_value"]
            out["declared_blank"] = d["blank"]
            # THE TRAP THIS EXISTS FOR: one `-detailed` call declared 308
            # objects and returned 41 rows.  A count that does not match the
            # rows parsed is a truncated read, not a small world.
            out["truncated"] = (d["zdos"] != len(rows))
        return out


def _unescape(v: str) -> str:
    """Undo the harness's backslash escaping.  Sign text is MULTI-LINE
    ("Peak Watchtower\\nNE 2164 m"), and a check that binds a board to the
    arch it NAMES has to compare the real string, not a mangled one."""
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            nxt = v[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t",
                        "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


# The FIELD selectors this gate needs.  `prefab:key` - a portal pairs on its
# own `tag` and a guidepost binds by its own `text`, and neither is derivable
# from a position.
ZDO_STRING_SELECTORS = "portal_wood:tag,sign:text,sign_notext:text"


def strings(cache: Path, fp: str, args=None) -> Strings:
    """The harness's ZDO FIELD census for this save - TAKEN IF IT IS MISSING.

    Nothing is not a pass.  A section that needs a string and finds none must
    REFUSE and name the missing instrument; it must never conclude that a
    world with no readable tags has no untagged portals.

    BUT A REFUSAL NOBODY CAN ACT ON IS A GAP WEARING A REFUSAL, and this
    reader was one.  Every other instrument here ACQUIRES its own reading -
    `census` runs the harness when `zdos.tsv` is absent, `ground` runs it per
    batch - and this one only ever read whatever happened to already be on
    disk.  So on any save whose cache had not been populated by hand the
    portals section refused with "no ZDO FIELD census for this save", named
    the exact command to fix it, and no run would ever take it: MEASURED on
    Ulfsland save 2c7011d2, that was the section's ONLY refusal and the world
    behind it was unmeasured rather than wrong.  It now runs the same harness
    under the same lock, and refuses only when the READING fails.

    Two file names and two row kinds are accepted because the field census
    landed after this reader was written; both are the same instrument and
    the superset file wins when both exist.
    """
    def _read() -> tuple[list, dict, bool]:
        rows: list = []
        declared: dict[tuple[str, str], dict] = {}
        seen_file = False
        for stem in ("zdo_fields_all", "zdo_fields", "zdostr"):
            path = cache / fp / f"{stem}.tsv"
            if not path.exists():
                continue
            seen_file = True
            for line in path.read_text(errors="ignore").splitlines():
                f = line.split("\t")
                if len(f) < 7 or f[0] not in ("zdofield", "zdostr"):
                    continue
                rows.append((f[1], float(f[2]), float(f[3]), float(f[4]),
                             f[5], _unescape("\t".join(f[6:]))))
            meta = cache / fp / f"{stem}.meta.tsv"
            if meta.exists():
                for line in meta.read_text(errors="ignore").splitlines():
                    f = [q for q in line.split("\t") if q != ""]
                    if not f or f[0] != "zdo_field_population" or len(f) < 9:
                        continue
                    # <prefab> <key> <type> zdos <N> with_value <M> blank <K>
                    declared[(f[1], f[2])] = dict(
                        type=f[3], zdos=int(f[5]), with_value=int(f[7]),
                        blank=(int(f[9]) if len(f) > 9
                               else int(f[5]) - int(f[7])))
            if rows:
                break
        return rows, declared, seen_file

    rows, declared, seen_file = _read()
    if not rows and args is not None:
        tsv = cache / fp / "zdostr.tsv"
        meta = cache / fp / "zdostr.meta.tsv"
        try:
            run_harness({"ZONES": "0,0",
                         "ZDO_STRINGS": ZDO_STRING_SELECTORS,
                         "ZDO_FIELDS": str(tsv)}, meta,
                        args.vh_src, args.world_src, args.world,
                        args.sandbox, recheck=tsv)
        except Exception as exc:                           # noqa: BLE001
            sys.stderr.write(f"[accept] the ZDO FIELD census could not be "
                             f"read: {type(exc).__name__}: {exc}\n")
        rows, declared, seen_file = _read()
    s = Strings(rows)
    s.declared = declared
    s.available = seen_file
    return s


# ---------------------------------------------------------------------------
# INSTRUMENT 2 - the ground the engine draws, read back from the save
# ---------------------------------------------------------------------------
def zone_of(x: float, z: float) -> tuple[int, int]:
    return (int(math.floor((x + ZONE_SIZE / 2) / ZONE_SIZE)),
            int(math.floor((z + ZONE_SIZE / 2) / ZONE_SIZE)))


def zones_containing(x: float, z: float) -> list[tuple[int, int]]:
    """Every zone whose 65x65 lattice holds this sample, not just the one the
    position belongs to.  Neighbouring zones SHARE their edge samples, and a
    step that only exists because two zones disagree on a shared edge is
    precisely the kind of defect the old check excluded by construction."""
    zx0, zz0 = zone_of(x, z)
    out = []
    for zx in (zx0 - 1, zx0, zx0 + 1):
        for zz in (zz0 - 1, zz0, zz0 + 1):
            if (abs(x - zx * ZONE_SIZE) <= 32.0 + 1e-9
                    and abs(z - zz * ZONE_SIZE) <= 32.0 + 1e-9):
                out.append((zx, zz))
    return out


# How far a TerrainComp op can reach.  MEASURED off the ledger, not assumed:
# all 1403 terrain ops in this world - 1259 road_segment, 135 site_pad,
# 5 harbour_place, 4 portal_hall_pad - carry last_op_radius EXACTLY 32.0 m
# with last_op_point EXACTLY on their own zone centre (1403/1403, zero
# exceptions, RoadsRemeasure).  A 32 m disc on a zone centre is INSCRIBED in
# that zone's own 64 m square, so on THIS world no op reaches out of its own
# zone.  The halo is one zone because an op written off-centre, or at a
# larger radius, would reach exactly that far - it is sized to the reach the
# game allows, not to the reach this world happens to use.
TERRAIN_REACH_M = 32.0
HALO_ZONES = int(math.ceil(TERRAIN_REACH_M / ZONE_SIZE))          # 1


def zones_to_spawn(x: float, z: float) -> list[tuple[int, int]]:
    """Every zone that must be LOADED for this sample's height to be final.

    NO DEFECT OF THIS CLASS HAS BEEN DEMONSTRATED, AND THIS COMMENT HAS
    NAMED THE WRONG CAUSE TWICE.  It is written out in full because a fix
    carrying an invented mechanism is how the next session inherits a wrong
    model of its own instrument.

    The halo was added for a 0.1632 m disagreement at station
    G|-836.500|7734.500 (GateStructures).  First this comment said a
    TerrainComp in a neighbouring zone contributed only when that neighbour
    was spawned; RoadsRemeasure measured that false - the six ops within
    128 m are all radius 32.0, the two nearest are 10.51 m away in the
    station's OWN zone, the next are 60.25 m and 80.69 m, and the
    neighbouring zone's lattice stops 28 m short.  Then it said ordering or
    idempotence; GateStructures measured THAT false too - both readings came
    from THE SAME FILE, THE SAME RUN and THE SAME spawned zone set, with an
    applied delta of 0.0000 in both, so there was never a comp to reach, to
    order or to re-apply.  The real cause was two DIFFERENT QUERIES 0.0001 m
    apart collapsed into one label by rounding, resolving to two different
    lattice vertices (see `snap` below).

    SO WHY IS THE HALO STILL HERE.  Not because it fixed anything measured -
    it did not, and nobody should say it did.  It is retained because it
    makes the spawned set around a point IDENTICAL whether that point is
    read alone or with fifty thousand others, so a batch boundary cannot
    move a height BY CONSTRUCTION rather than by luck.  The evidence that
    the old reader was in fact lucky rather than wrong is RoadsRemeasure's
    two-cut control: two complete pre-halo reads with different batch cuts
    (5 batches against 4) agree on 21,979 of 21,979 road stations, max
    disagreement 0.0000 m.  That is evidence of batch-independence, NOT
    evidence of a bug.  The cost is one extra batch per read; the benefit is
    that the property no longer depends on how somebody cut the work.
    """
    out: set[tuple[int, int]] = set()
    for zx, zz in zones_containing(x, z):
        for dx in range(-HALO_ZONES, HALO_ZONES + 1):
            for dz in range(-HALO_ZONES, HALO_ZONES + 1):
                out.add((zx + dx, zz + dz))
    return sorted(out)


# ---------------------------------------------------------------------------
# THE GROUND READING IS A VERTEX HEIGHT, NOT AN INTERPOLATED SURFACE
# ---------------------------------------------------------------------------
# MEASURED (GateStructures, frozen save f803b9a3): two queries 0.0001 m apart
# at (-836.5, 7734.5) returned 30.4490 m and 30.2858 m - 0.1632 m apart, from
# the same file, the same run and the same spawned zones.  `Heightmap`
# quantises a query to the nearest lattice vertex (`WorldToVertex` rounds), so
# the reading is a STEP FUNCTION on the 1 m lattice.  A query on the exact
# half-lattice line is equidistant from two vertices and flips on a nudge too
# small to print, and Valheim snaps build pieces to 0.5 m, so any section that
# samples ground AT PIECE POSITIONS puts its queries on that line constantly:
# 413 of 38,867 in one structures batch.
#
# Two consequences, and every section owes both:
#   SNAP    ask for the vertex, so the same label is the same question every
#           time.  `gpoint` returns the label AND the snapped coordinate
#           together so the two cannot drift apart - that pattern is
#           GateStructures', moved here so there is one implementation.
#   SAY SO  a number from this instrument is the height of a lattice VERTEX
#           within 0.5 m of where you asked.  Reporting it as the height at
#           your exact xz overstates the instrument's own resolution, and the
#           error bar is the LOCAL VERTEX DIFFERENCE - 0.1632 m at that
#           station, not a rounding epsilon.
LATTICE_M = 1.0


def snap(x: float, z: float) -> tuple[float, float]:
    """The lattice vertex this query actually reads, chosen deterministically.

    Half-way cases go UP, always, rather than to whichever vertex a float
    error picks.  Which side is arbitrary; that it is the SAME side every
    time is the whole point.
    """
    return (math.floor(x / LATTICE_M + 0.5) * LATTICE_M,
            math.floor(z / LATTICE_M + 0.5) * LATTICE_M)


# ---------------------------------------------------------------------------
# THE SECTION AUTHOR'S FIRST QUESTION
# ---------------------------------------------------------------------------
# A MINIMUM OVER A SET LIVES ON A VERTEX.  A STEP BETWEEN TWO READINGS DOES
# NOT.
#
# So, before you sample anything: does your check take an EXTREMUM over a
# region, or a DIFFERENCE between two places?
#
#   extremum    sample VERTEX-COMPLETE over the region.  The extreme is then
#               CONTAINED in your sample set rather than approximated by it,
#               because the reading is constant within a cell and the
#               surface's own extremes sit on the lattice.
#   difference  walk the MESH EDGES the walker actually crosses.  A diagonal
#               is two 1 m edges through a shared corner, not one 1.414 m
#               step, and the corner is off your centreline - so it has to
#               be READ, and a corner nobody read makes that transition
#               UNRESOLVED, never clean.
#
# Derived independently in three populations on one night: structures'
# per-piece ground columns (a slab resting on one corner read as floating by
# up to 1.5 m from a centre-only sample), harbours' footprint minimum and
# route refinement (a 1 m bar between two 2 m samples read as a 13 m clear
# channel), and roads' lattice-edge walk (2,878 of 21,954 adjacent pairs
# scoring 0.000 by construction).  TWO OF THE THREE WERE NEARLY SHIPPED AS
# DISCLOSURES INSTEAD OF FIXES - "we report a vertex height" rather than "we
# sample what the check actually needs".
#
# Every blind check this project produced was a difference measured as though
# it were an extremum, or an extremum approximated by one sample.


def gpoint(x: float, z: float, prefix: str = "g") -> tuple[str, float, float]:
    """(label, x, z) for a ground query: ONE LABEL IS ONE QUESTION.

    The defect this exists to stop: a section labelled by the rounded
    coordinate and queried with the unrounded one, so two queries 0.0001 m
    apart shared a label, resolved to different vertices, and the ground
    reader's own cross-check reported the instrument as irreproducible.  The
    label is derived from the SNAPPED coordinate that is also sent, so they
    cannot disagree.
    """
    sx, sz = snap(x, z)
    return (f"{prefix}|{sx:.0f}|{sz:.0f}", sx, sz)


# ---------------------------------------------------------------------------
# THE STALE-HIT GUARD
# ---------------------------------------------------------------------------
# A wrong cache reuse is the one error worse than a slow read: it is silent,
# and it poisons every number downstream of it.  So the epoch key is checked
# rather than trusted, and the check is built around the SHAPE of the thing
# it hunts, which was measured before it was written.
#
# WHY IT IS NOT A UNIFORM RANDOM SAMPLE.  The obvious guard - "re-read a
# small random sample of cached vertices and refuse on any disagreement" -
# was MEASURED against the real known-bad pair on this world, a087af62 (save
# seq 5416) against 245daa7f (seq 5420, with terrain_write 5417 and 5419 in
# between).  11 of 451,702 shared vertices moved.  A uniform sample of n
# catches that with probability 1-(1-11/451702)^n:
#
#     n = 48     0.12 %
#     n = 256    0.62 %
#     n = 4096   9.5  %
#
# i.e. the obvious guard is not a weak check, it is NO CHECK, and it would
# have printed "guard passed, 48 of 48 agreed" over a poisoned cache.  A
# terrain write here is 1.527 cubic metres across ten sites; it is a needle
# and the haystack is half a million road stations.
#
# WHAT THIS GUARD DOES INSTEAD.  A bed only moves where something acted, and
# every record carries WHERE it acted.  The population sampled is therefore
# the cached vertices lying inside the extents of the records that have been
# classified INERT since the last save that shared this directory - which is
# exactly the hypothesis the key rests on, sampled exactly where it would
# fail.  Detection probability against a mis-classified record is 1 wherever
# the cache holds a vertex in that record's extent, not 0.12 %.  A uniform
# tail is still drawn, but it is declared for what it is: a backstop against
# a terrain edit nobody recorded, with the weak power computed above.
#
# THE THIRD GUARD IS FREE AND IS THE STRONGEST OF THE THREE: `ground()`
# already ABORTS when one station reads two heights, and re-keying makes
# that test span SAVES instead of dying inside one fingerprint directory.
# Every point any later run reads is checked against the cached value, for
# no engine time at all.
GROUND_GUARD_SAMPLE = 48
# Metres around a record's declared point that count as "where it acted"
# when the record declares no radius of its own.  A zone is 64 m; a record
# that says only "I happened at (x, z)" gets the zone-sized benefit of the
# doubt, because under-reaching here means sampling away from the defect.
GROUND_GUARD_DEFAULT_REACH_M = 32.0
_GUARDED: set = set()


def ground_dir(cache: Path, epoch: TerrainEpoch) -> Path:
    return Path(cache) / "ground" / epoch.key


def _occupants_file(cache: Path) -> Path:
    return Path(cache) / "ground" / "occupants.tsv"


def ground_occupants(cache: Path) -> list[dict]:
    """Every (terrain epoch, save) pair this cache has ever served, world
    wide.  World wide and not per-epoch because the refusals below are about
    a save appearing under TWO keys, which no single epoch can see."""
    f = _occupants_file(cache)
    out: list[dict] = []
    if not f.exists():
        return out
    raw = f.read_text(errors="ignore")
    lines = raw.splitlines()
    if lines and not raw.endswith("\n"):
        lines.pop()          # a row another run is still appending
    for n, line in enumerate(lines, 1):
        r = line.split("\t")
        # A ROW THAT WILL NOT PARSE IS NOT SKIPPED.  This list exists to
        # catch one save wearing two terrain epochs; a reader that steps
        # over the rows it cannot read is a check that gets quieter the more
        # damaged its evidence is, which is the wrong direction.
        try:
            out.append({"key": r[0], "fp": r[1], "generation": int(r[2]),
                        "head_seq": int(r[3]), "ts": r[4]})
        except (IndexError, ValueError) as exc:
            raise RuntimeError(
                f"{f} line {n} will not parse ({exc}): this file is the only "
                "record of which saves share a terrain epoch, so an "
                "unreadable row is a FAILED READ and not an empty one.  "
                f"Repair or delete {f} and the epoch directories with it."
            ) from None
    return out


def ground_admit(cache: Path, epoch: TerrainEpoch) -> list[dict]:
    """REFUSE a save that cannot belong to this terrain epoch, and name both
    epochs when it happens.

    A save is one world at one instant, so it is in exactly ONE terrain
    state.  Two ways that is violated, both cheap to see and both fatal:

      the same CONTENT fingerprint filed under two keys - the ledger moved
        under a save that did not, which is what reading a frozen copy after
        a terrain write looks like;
      the same SAVE GENERATION (`N` in `_main.N.fwl2`, bumped by
        `SaveSystem::GetSaveNumber` on every save) filed under two keys -
        the same thing, seen through the save's own counter, and it survives
        a byte-level difference the content hash would call a new save.

    This does NOT catch the first-ever read of a stale copy: nothing on disk
    distinguishes it.  Said here rather than left as a silence.
    """
    prior = ground_occupants(cache)
    for row in prior:
        if row["key"] == epoch.key:
            continue
        if epoch.save_fp and row["fp"] == epoch.save_fp:
            raise RuntimeError(
                f"REFUSING: save {epoch.save_fp[:16]} is already recorded in "
                f"terrain epoch {row['key']} (ledger head {row['head_seq']}) "
                f"and this run files it under {epoch.key} (head "
                f"{epoch.head_seq}).  One save is one terrain state; two "
                "means the ledger moved under a save that did not, and the "
                "ground readings of the two would be joined by one verdict.")
        if epoch.save_generation >= 0 and \
                row["generation"] == epoch.save_generation:
            raise RuntimeError(
                f"REFUSING: save generation {epoch.save_generation} "
                f"(_main.{epoch.save_generation}.fwl2) is already recorded "
                f"in terrain epoch {row['key']} and this run files it under "
                f"{epoch.key}.  The generation counter is the save's own "
                "identity; the same save cannot be in two terrain states.")
    return prior


def ground_record_occupant(cache: Path, epoch: TerrainEpoch) -> None:
    f = _occupants_file(cache)
    f.parent.mkdir(parents=True, exist_ok=True)
    for row in ground_occupants(cache):
        if row["key"] == epoch.key and row["fp"] == epoch.save_fp:
            return
    with f.open("a") as fh:
        fh.write(f"{epoch.key}\t{epoch.save_fp}\t{epoch.save_generation}\t"
                 f"{epoch.head_seq}\t{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")


def record_extents(rec: dict) -> list[tuple[float, float, float]]:
    """(x, z, radius) discs a record declares it acted in, off its OWN
    params.  Empty when it declares no place, which the caller must treat as
    "could be anywhere" rather than "nowhere"."""
    p = rec.get("params") or {}
    r = GROUND_GUARD_DEFAULT_REACH_M
    for k in ("radius_m", "reach_m", "max_m", "guard_radius_m"):
        v = p.get(k)
        if isinstance(v, (int, float)) and v > 0:
            r = max(r, float(v))
    out: list[tuple[float, float, float]] = []

    def add(x, z):
        try:
            out.append((float(x), float(z), r))
        except (TypeError, ValueError):
            pass

    pos = p.get("pos")
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        add(pos[0], pos[1])
    elif isinstance(pos, (list, tuple)) and len(pos) >= 3:
        add(pos[0], pos[2])
    c = p.get("centre")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        add(c[0], c[1])
    a = p.get("anchor")
    if isinstance(a, dict):
        add(a.get("x"), a.get("z"))
    for e in (p.get("entries") or []):
        if isinstance(e, dict):
            ec = e.get("centre")
            if isinstance(ec, (list, tuple)) and len(ec) >= 2:
                add(ec[0], ec[1])
    return out


def ground_guard(cache: Path, epoch: TerrainEpoch, args,
                 known: dict[str, float], since_seq: int,
                 sample: int = GROUND_GUARD_SAMPLE) -> dict:
    """Re-read cached vertices FROM THE ENGINE and refuse the hit on any
    disagreement.  Returns what it measured, including its own cost."""
    if not known:
        return {"sampled": 0, "why": "nothing cached to guard"}
    coords: dict[str, tuple[float, float]] = {}
    for k in known:
        a, _, b = k.partition("|")
        try:
            coords[k] = (float(a), float(b))
        except ValueError:
            continue
    suspect: list[str] = []
    untested = 0
    for rec in ledger_records(Path(epoch.ledger)):
        seq = rec.get("seq")
        if not isinstance(seq, int) or seq <= since_seq:
            continue
        if record_moves_bed(rec):
            continue                      # not the hypothesis under test
        untested += 1
        for x, z, r in record_extents(rec):
            rr = r * r
            for k, (px, pz) in coords.items():
                if (px - x) ** 2 + (pz - z) ** 2 <= rr:
                    suspect.append(k)
    # Deterministic, so the guard is reproducible and two runs of it are
    # comparable rather than two different questions.
    rng = random.Random(f"{epoch.key}|{epoch.save_fp}|{since_seq}")
    targeted = sorted(set(suspect))
    rng.shuffle(targeted)
    targeted = targeted[:sample]
    zs: set[tuple[int, int]] = set()
    for k in targeted:
        zs.update(zones_to_spawn(*coords[k]))
    # THE BACKSTOP IS BOUNDED BY ZONES, NOT BY POINTS, because zones are
    # what a read costs.  48 stations drawn uniformly off a 400-station road
    # are 48 different zones; the targeted points are contiguous and are
    # three or four.  So the targeted set is never dropped - it is the half
    # with detection power - and the uniform tail stops at the same zone
    # budget one ordinary batch is allowed.
    budget = max(4, int(getattr(args, "zone_batch", 110)) // 4)
    rest = sorted(set(coords) - set(targeted))
    rng.shuffle(rest)
    backstop = []
    for k in rest:
        if len(targeted) + len(backstop) >= sample:
            break
        z = set(zones_to_spawn(*coords[k]))
        if len(zs | z) > budget and backstop:
            continue
        zs |= z
        backstop.append(k)
    chosen = targeted + backstop
    if not chosen:
        return {"sampled": 0, "why": "no cached vertex to re-read"}
    pts = [(f"guard{i}", coords[k][0], coords[k][1])
           for i, k in enumerate(chosen)]
    outdir = ground_dir(cache, epoch)
    out = outdir / f"guard_{epoch.save_fp[:16]}_{since_seq}.tsv"
    t0 = time.time()
    if not out.exists():
        pf = out.with_suffix(".pts")
        pf.write_text("".join(f"{a}\t{b:.4f}\t{c:.4f}\n" for a, b, c in pts))
        sys.stderr.write(
            f"[accept] ground guard: re-reading {len(pts)} cached vertices "
            f"({len(targeted)} inside the extents of {untested} records this "
            f"key calls inert, {len(backstop)} uniform backstop) over "
            f"{len(zs)} zones\n")
        run_harness({"ZONES": ";".join(f"{a},{b}" for a, b in sorted(zs)),
                     "POINTS_FILE": str(pf)}, out,
                    args.vh_src, args.world_src, args.world, args.sandbox)
    secs = time.time() - t0
    disagree = []
    read = 0
    for line in out.read_text().splitlines():
        f = line.split("\t")
        if f[0] != "point" or len(f) < 6:
            continue
        i = int(f[1][5:])
        read += 1
        was = known[chosen[i]]
        if abs(was - float(f[5])) > 1e-3:
            disagree.append((chosen[i], was, float(f[5])))
    if read != len(pts):
        raise RuntimeError(
            f"ground guard asked for {len(pts)} vertices and got {read}: a "
            "guard that did not read is not a guard that agreed")
    if disagree:
        raise RuntimeError(
            f"REFUSING the ground cache at terrain epoch {epoch.key}: "
            f"{len(disagree)} of {read} re-read vertices disagree with the "
            f"cached reading, first (xz, cached, engine) {disagree[0]}.  The "
            "terrain moved without a ledger record that says so, so the key "
            "cannot see it.  Delete "
            f"{ground_dir(cache, epoch)} and re-read, and find the write.")
    return {"sampled": read, "targeted": len(targeted),
            "backstop": len(backstop), "untested_records": untested,
            "zones": len(zs), "seconds": round(secs, 1)}


def ground(cache: Path, epoch: TerrainEpoch,
           pts: list[tuple[str, float, float]], args) -> dict[str, float]:
    """applied bed y for every labelled point, in batches of zones.

    Batched because a run must spawn every zone its points fall in and a whole
    road network is several hundred zones; the batches are cut on the ORDERED
    station list so a batch is a contiguous run of road, and they OVERLAP by
    one station so that the two readings of the shared station are a free
    cross-run agreement check.

    THE BATCH MUST NOT BE ABLE TO CHANGE THE ANSWER.  Every zone is spawned
    with a one-zone halo (see `zones_to_spawn`) and the batch budget is spent
    on the HALOED set, so the zones loaded around a point are the same
    whether it is read alone or with fifty thousand others.  Cached readings
    taken before the halo are not reused: their tag carries the halo radius,
    so a pre-halo file is a different question's answer and is left on disk
    rather than silently trusted.

    WHAT THIS RETURNS IS A VERTEX HEIGHT.  `Heightmap` quantises to the
    nearest 1 m lattice vertex, so a caller asking at an arbitrary xz gets
    the vertex within 0.5 m of it, and a caller asking ON the half-lattice
    line is asking an ambiguous question whose two answers differ by the
    local vertex difference - 0.1632 m at the one station where it was
    measured.  This function does NOT silently move a caller's points: a
    label belongs to the caller's own population and renaming it here would
    be the same class of fault as the mislabelling that found this.  It
    COUNTS the ambiguous ones and says so, and `gpoint()` is here for every
    caller that should be asking on the lattice in the first place.

    AND IT IS FILED UNDER THE TERRAIN, NOT UNDER THE SAVE.  See the
    `TerrainEpoch` block above for the measurement; the short version is
    that this function's answer is a function of the terrain alone and it
    used to be thrown away by every bridge plank anybody placed.
    """
    outdir = ground_dir(cache, epoch)
    outdir.mkdir(parents=True, exist_ok=True)
    got: dict[str, float] = {}
    disagreements: list[tuple] = []

    on_the_line = [lb for lb, x, z in pts
                   if abs(abs(x) % 1.0 - 0.5) < 1e-6
                   or abs(abs(z) % 1.0 - 0.5) < 1e-6]
    if on_the_line:
        sys.stderr.write(
            f"[accept] WARNING {len(on_the_line)} of {len(pts)} requested "
            "points sit on the half-lattice line, where Heightmap is "
            "equidistant from two vertices and the reading can flip by the "
            f"local vertex difference; first {on_the_line[0]}.  Use "
            "instruments.gpoint() to ask on the lattice.\n")

    # THE CACHE IS PER POINT, NOT PER BATCH.  A batch file is only reusable
    # by a run that cuts its batches identically, so `--zone-batch 110` and
    # `--zone-batch 220` over the SAME 21,979 stations missed every tag and
    # bought a second full read - and two sections that walk the same road
    # (roads and lighting) paid twice for one answer.  With the halo the
    # reading is a property of the POINT and not of the company it was read
    # in, so it is stored that way: one index keyed by xz, shared by every
    # section and every batch size.  The halo radius is in the file name
    # because a pre-halo reading answers a different question.
    index = outdir / f"points_halo{HALO_ZONES}.tsv"

    def _whole_lines(p: Path) -> list[str]:
        """Every COMPLETE line.  The index is appended to, line by line, by
        concurrent runs of this gate; a reader that takes the last partial
        line gets a TRUNCATED FLOAT, and `12.3456\t-78.9\t41.2` cut short is
        not a parse error, it is a plausible wrong height.  A record that is
        not terminated has not been written yet."""
        raw = p.read_text(errors="ignore")
        lines = raw.splitlines()
        if lines and not raw.endswith("\n"):
            lines.pop()
        return lines

    def load_index() -> dict[str, float]:
        out: dict[str, float] = {}
        if index.exists():
            for line in _whole_lines(index):
                f = line.split("\t")
                if len(f) >= 3:
                    out[f"{float(f[0]):.3f}|{float(f[1]):.3f}"] = float(f[2])
        return out

    # THE ONE-TIME IMPORT OF THIS SAVE'S OWN PRE-EPOCH READINGS.  The cache
    # holds 81 fingerprint directories of ground that was paid for under the
    # old key.  Nothing on disk says which terrain epoch most of them belong
    # to - so they are NOT swept in.  THIS run's save is the one exception:
    # it is the save whose ledger produced this key, so its own old
    # directory is in this epoch by construction, and importing it is free.
    legacy = Path(cache) / epoch.save_fp / "ground" / index.name
    if epoch.save_fp and legacy.exists() and not index.exists():
        lines = _whole_lines(legacy)
        index.write_text("".join(ln + "\n" for ln in lines))
        sys.stderr.write(
            f"[accept] ground: imported {len(lines)} vertices already read "
            f"at save {epoch.save_fp[:16]} into terrain epoch {epoch.key}\n")

    known: dict[str, float] = load_index()

    # ADMIT, THEN GUARD, THEN SERVE.  Once per process per (epoch, save).
    #
    # ADMISSION AND RECORDING HAPPEN EVEN ON A COLD DIRECTORY, and that is
    # not bookkeeping.  MEASURED while writing this: with the record skipped
    # when there was nothing cached, the FIRST save in an epoch was never
    # written down - so it could later be re-offered under a different key
    # with nothing to collide with, and the `since_seq` the guard aims at
    # fell back to the ledger head and targeted NOTHING.  The occupant list
    # is what makes both of those work, so it is written whether or not this
    # run got a hit.
    if epoch.key + epoch.save_fp not in _GUARDED:
        _GUARDED.add(epoch.key + epoch.save_fp)
        prior = ground_admit(cache, epoch)
        mine = [r for r in prior if r["key"] == epoch.key]
        if known and not any(r["fp"] == epoch.save_fp for r in mine):
            # THE WINDOW IS THE EPOCH'S OWN INERT TAIL, not "records since
            # the last occupant".  MEASURED while writing this: a run's
            # `head_seq` is the LIVE ledger head, so two saves eight records
            # apart both record head 5428 and the occupant-derived window is
            # empty - the guard would have sampled 48 uniform points and
            # claimed to have checked something.  Every record after
            # `last_move_seq` is one this key calls inert, and those are
            # exactly the records the key's correctness rests on.
            g = ground_guard(cache, epoch, args, known, epoch.last_move_seq)
            sys.stderr.write(f"[accept] ground guard: {g}\n")
        ground_record_occupant(cache, epoch)

    def key(x: float, z: float) -> str:
        return f"{x:.3f}|{z:.3f}"

    todo = []
    for label, x, z in pts:
        k = key(x, z)
        if k in known:
            got[label] = known[k]
        else:
            todo.append((label, x, z))
    if todo and len(todo) < len(pts):
        sys.stderr.write(f"[accept] ground: {len(pts) - len(todo)} of "
                         f"{len(pts)} points already read in terrain epoch "
                         f"{epoch.key} (last bed-moving record seq "
                         f"{epoch.last_move_seq}); {len(todo)} to read\n")

    batches: list[list[tuple[str, float, float]]] = []
    cur: list[tuple[str, float, float]] = []
    curz: set[tuple[int, int]] = set()
    for p in todo:
        z = set(zones_to_spawn(p[1], p[2]))
        if cur and len(curz | z) > args.zone_batch:
            batches.append(cur + [p])          # overlap by one station
            cur, curz = [cur[-1], p], set(zones_to_spawn(*cur[-1][1:])) | z
        else:
            cur.append(p)
            curz |= z
    if cur:
        batches.append(cur)
    fresh: dict[str, tuple[float, float, float]] = {}
    for i, batch in enumerate(batches):
        tag = hashlib.sha256(
            (f"halo{HALO_ZONES};" + "".join(
                f"{a}|{b:.3f}|{c:.3f};" for a, b, c in batch)
             ).encode()).hexdigest()[:16]
        out = outdir / f"b{i:03d}_{tag}.tsv"
        if not out.exists():
            pf = outdir / f"b{i:03d}_{tag}.pts"
            pf.write_text("".join(f"{a}\t{b:.4f}\t{c:.4f}\n"
                                  for a, b, c in batch))
            zs: set[tuple[int, int]] = set()
            for _, x, z in batch:
                zs.update(zones_to_spawn(x, z))
            sys.stderr.write(f"[accept] ground batch {i + 1}/{len(batches)}: "
                             f"{len(batch)} stations, {len(zs)} zones\n")
            t0 = time.time()
            # TWO IN-LOCK CHECKS, both because the tests above happened
            # OUTSIDE the lock.  `recheck` catches another run that asked the
            # IDENTICAL question; `skip_if` catches the one the tag cannot
            # see - another run whose point set merely OVERLAPS ours and has
            # since read every point in this batch.  `todo` was frozen before
            # we queued, so without this we pay full price for points that
            # landed in the index while we waited.
            def covered(batch=batch):
                now = load_index()
                if all(key(x, z) in now for _l, x, z in batch):
                    known.update(now)
                    return True
                return False

            run_harness({"ZONES": ";".join(f"{a},{b}" for a, b in sorted(zs)),
                         "POINTS_FILE": str(pf)}, out,
                        args.vh_src, args.world_src, args.world, args.sandbox,
                        recheck=out, skip_if=covered)
            sys.stderr.write(f"[accept]   {time.time() - t0:.0f}s\n")
        if not out.exists():
            # The batch was served from the index by another run while we
            # queued: take those points from the index, not from a file that
            # was never written.
            for label, x, z in batch:
                k = key(x, z)
                if k in known:
                    got[label] = known[k]
            continue
        where = {a: (b, c) for a, b, c in batch}
        batch_fresh: dict[str, tuple[float, float, float]] = {}
        for line in out.read_text().splitlines():
            f = line.split("\t")
            if f[0] != "point" or len(f) < 6:
                continue
            y = float(f[5])
            if f[1] in got and abs(got[f[1]] - y) > 1e-3:
                disagreements.append((f[1], got[f[1]], y))
            got[f[1]] = y
            xz = where.get(f[1])
            if xz is not None:
                k = key(*xz)
                # THE OVERLAP CHECK, NOW ACROSS RUNS AND CUTS.  Two batches
                # share a station on purpose; the index makes the same test
                # hold between a reading taken today and one taken in another
                # run at another batch size.  A disagreement is an abort, not
                # an average.
                if k in known and abs(known[k] - y) > 1e-3:
                    disagreements.append((k, known[k], y))
                if k in fresh and abs(fresh[k][2] - y) > 1e-3:
                    disagreements.append((k, fresh[k][2], y))
                fresh[k] = (xz[0], xz[1], y)
                batch_fresh[k] = fresh[k]
        # PUBLISH EACH BATCH AS IT LANDS, not at the end of the call.
        # MEASURED tonight: a roads run was stopped part-way through a
        # 21,979-point read, and although every batch file was on disk the
        # index had none of them - the next run re-read 1,482 points that
        # had already been paid for, because the index was written once at
        # the end.  A reading that exists on disk but is invisible to the
        # next run is the same waste as not having taken it.
        if batch_fresh:
            with index.open("a") as fh:
                for x, z, y in batch_fresh.values():
                    fh.write(f"{x:.4f}\t{z:.4f}\t{y:.6f}\n")
            known.update({k: v[2] for k, v in batch_fresh.items()})
    if disagreements:
        raise RuntimeError(
            "the same station read two different heights in two runs - the "
            f"instrument is not reproducible: {disagreements[:5]}")
    missing = [lb for lb, _x, _z in pts if lb not in got]
    if missing:
        raise RuntimeError(
            f"{len(missing)} points were requested and never read, first "
            f"{missing[0]}; a missing height is a failed read, not a hole "
            "in the world")
    return got


# ---------------------------------------------------------------------------
# INSTRUMENT 3 - the GENERATED bed, from the seed, with no save in it
# ---------------------------------------------------------------------------
def generated(cache: Path, args, pts: list[tuple[str, float, float]],
              step_m: float = 1.0, tile_m: float = 128.0
              ) -> dict[str, float]:
    """The bed the SEED makes, before anything was written - or a refusal.

    WHY A SECOND HEIGHT INSTRUMENT EXISTS.  "Was this water before we built
    here" cannot be asked of the save: a fill that already drained a channel
    reads as dry land, and the defect becomes invisible.  That is the same
    self-comparison that printed WALKABLE on sixteen broken segments.  The
    generated bed is the only reference that is not the artefact under test,
    and `run_patchscan.sh` never opens a save - it takes a SEED.

    SURFACE=heightmap, not worldgen: `HeightmapBuilder`'s per-zone
    corner-biome blend is the ground the engine draws, and PatchScan.cs's own
    header records the two differing by 10.08 m on Ulfsland zone (22,24).

    ONE REQUEST ROW PER TILE, NOT PER POINT.  PatchScan scans a square field
    around a centre, so a 16,641-point lattice is ONE row, not 16,641 server
    launches.  Points are snapped to a `tile_m` grid and each occupied tile
    is requested once, with a one-step margin so a point on a tile edge is
    still inside its field for the bilinear read.

    It spawns its own server on the same global port as the save harness, so
    it takes the SAME lock: the two instruments cannot collide with each
    other any more than with another agent.

    Raises rather than returning partial data.  A draining check that cannot
    reach its reference must REFUSE, never pass.
    """
    sh = JUMPSTART / "blueprints" / "run_patchscan.sh"
    if not sh.exists():
        raise RuntimeError(
            f"the generated-bed instrument is unavailable: {sh} is missing; "
            "refusing to answer 'was this water' from the save, which is the "
            "artefact under test")
    sys.path.insert(0, str(JUMPSTART))
    from crossings import water as W            # noqa: PLC0415

    seed = args.seed
    half = tile_m / 2.0 + 2.0 * step_m          # margin for the bilinear read
    tiles: dict[tuple[int, int], tuple[float, float]] = {}
    for _label, x, z in pts:
        key = (int(math.floor(x / tile_m)), int(math.floor(z / tile_m)))
        tiles[key] = ((key[0] + 0.5) * tile_m, (key[1] + 0.5) * tile_m)
    if not tiles:
        return {}
    outdir = cache / "generated"
    outdir.mkdir(parents=True, exist_ok=True)
    spec = "".join(f"{a},{b};" for a, b in sorted(tiles))
    tag = hashlib.sha256(
        f"{seed}|heightmap|{tile_m:g}|{step_m:g}|{spec}".encode()
    ).hexdigest()[:16]
    binp = outdir / f"{seed}_{tag}.bin"
    if not binp.exists():
        req = outdir / f"{seed}_{tag}.req"
        req.write_text("".join(
            f"t{a}_{b}\t{cx:.4f}\t{cz:.4f}\t{half:g}\t{step_m:g}\n"
            for (a, b), (cx, cz) in sorted(tiles.items())))
        env = dict(os.environ)
        env.update(VH_SRC=args.vh_src, SEED=seed, SURFACE="heightmap",
                   REQ=str(req), OUT=str(binp),
                   SANDBOX=os.environ.get("PATCHSCAN_SANDBOX",
                                          "/tmp/patchscan/vh"))
        sys.stderr.write(f"[accept] generated bed: {len(tiles)} tiles of "
                         f"{tile_m:g} m at {step_m:g} m, seed {seed}\n")
        with harness_lock(f"patchscan -> {binp}") as _port:
            if binp.exists() and binp.stat().st_size > 0:
                # Another run produced this field while we queued; the same
                # check-then-act race the ground reader had.
                sys.stderr.write(f"[accept] {binp.name} was produced while "
                                 "we queued; consuming it\n")
                cp = None
            else:
                t0 = time.time()
                cp = subprocess.run(["bash", str(sh)], env=env,
                                    capture_output=True, text=True,
                                    timeout=5400)
                sys.stderr.write(f"[accept]   {time.time() - t0:.0f}s\n")
        if not binp.exists() or binp.stat().st_size == 0:
            raise RuntimeError(
                f"run_patchscan.sh produced no generated field for "
                f"{len(tiles)} tiles: this is a FAILED READ, not a world "
                "without water.\n"
                + (cp.stderr[-2000:] if cp is not None else
                   "another run was expected to have produced it and did "
                   "not"))
    fields = W.load(binp)
    if len(fields) != len(tiles):
        raise RuntimeError(
            f"the generated field declares {len(fields)} patches for "
            f"{len(tiles)} requested tiles - a truncated read is refused "
            "rather than filled in")
    got: dict[str, float] = {}
    missing: list[tuple[str, float, float]] = []
    for label, x, z in pts:
        key = (int(math.floor(x / tile_m)), int(math.floor(z / tile_m)))
        f = fields.get(f"t{key[0]}_{key[1]}")
        if f is None or not f.contains(x, z, margin=step_m):
            missing.append((label, x, z))
            continue
        got[label] = f.height_at(x, z)
    if missing:
        raise RuntimeError(
            f"{len(missing)} points fell outside every generated tile, "
            f"first {missing[0]}; refusing a partial reference")
    return got


# ---------------------------------------------------------------------------
# POPULATION HELPERS shared by more than one section
# ---------------------------------------------------------------------------
def resample(nodes: list[tuple[float, float]], step: float
             ) -> list[tuple[float, float, float]]:
    """(s, x, z) every `step` metres along the polyline, endpoints included."""
    out: list[tuple[float, float, float]] = []
    s = 0.0
    nxt = 0.0
    for i in range(len(nodes) - 1):
        (x0, z0), (x1, z1) = nodes[i], nodes[i + 1]
        seglen = math.hypot(x1 - x0, z1 - z0)
        if seglen <= 0:
            continue
        while nxt <= s + seglen + 1e-9:
            t = (nxt - s) / seglen
            out.append((nxt, x0 + t * (x1 - x0), z0 + t * (z1 - z0)))
            nxt += step
        s += seglen
    if not out or abs(out[-1][0] - s) > 1e-6:
        out.append((s, nodes[-1][0], nodes[-1][1]))
    return out


# ---------------------------------------------------------------------------
# GEOMETRY - each prefab's own solid, rotated by the ZDO's own quaternion
# ---------------------------------------------------------------------------
def load_geometry() -> tuple[dict[str, list], set[str]]:
    """The solid corpus, and WHICH of its prefabs are BUILD PIECES.

    The split matters because the two halves are known to different
    precisions and a gate that mixes them is back to answering a question
    about structures with a count of something else.  `PieceGeometry.cs`
    records a BoxCollider / SphereCollider / CapsuleCollider by its own shape
    parameters - tight - but a MeshCollider only by `sharedMesh.bounds`, an
    axis-aligned box round an irregular boulder.  `rock4_forest` comes out as
    a 27.7 x 24.2 m slab, which is the rock's bounding box and emphatically
    not the rock.  Build pieces are the box-collider half AND the population
    the question "what is standing in my road" is actually about, so they
    decide the verdict; everything else is reported with its precision named.

    SINCE 2026-09-22 THE CORPUS SAYS WHICH IS WHICH, per collider, as the
    last element of every part row.  It had to: the paragraph above was
    TRUE AND UNENFORCEABLE, and the spawn gate refused a bed as "1.087 m
    INSIDE Beech1" on a tree whose pivot is 23.2 m away, because `Beech1`
    carries two MeshCollider bounds and NO capsule at all.  6,956 of the
    11,972 non-render colliders are `mesh`, so a reader that cannot tell a
    shape from its bounding box is guessing about 58 % of the world.

    WHICH FILE, and why that is an environment variable.  A corpus rebuild
    changes the population several sections count over - `build_pieces` is
    the filter `structures` uses to decide what a body is made of - so a
    candidate corpus has to be MEASURABLE BEFORE IT IS LANDED, or the only
    way to find out what it does to the verdict is to land it on top of
    everybody else's in-flight before/after.  `ACCEPT_PIECE_GEOMETRY` points
    this at a candidate; unset, it is the repo's own file and nothing moves.
    The path in use is printed, because a gate reading a corpus nobody can
    see from the report is exactly the silent-substitution failure this
    module exists to refuse.

    AND A TRIGGER VOLUME IS NOT A SOLID, SO THE GATE DOES NOT READ ONE AS
    ONE.  The dump keeps every non-trigger collider, and `piece_artisanstation`
    carries - beside its 2.65 x 1.03 x 1.10 m bench and its rack - a
    40 x 200 x 40 m capsule centred on its pivot that no walker has ever run
    into.  MEASURED on drive 2's frozen Fresh42 save a521b70356e7: the
    complete-station-hub pad reads 77.650 m at all four probe vertices
    (104, 351..354), EXACTLY its declared datum, and the settlements gate
    reported it UNMEASURED twice (S7 A, T6 A) because every one of those
    vertices sat inside that capsule, so `blocks_at_height` vetoed the ground
    and the stair and floor standing on it.  The same capsule put three spawn
    beds "11.4-19.0 m INSIDE piece_artisanstation".  `blueprints/
    base_geometry.Geometry` already drops these for pad clearance - on build
    pieces with more than one solid, any solid over 16 m tall; on this corpus
    that is piece_artisanstation, artisan_cart and BigCargoShip - so this reads
    the corpus THROUGH THAT RULE rather than writing a second one, and the
    builder and the gate see the same solids.
    """
    path = os.environ.get("ACCEPT_PIECE_GEOMETRY") or str(
        JUMPSTART / "blueprints" / "data" / "piece_geometry.json")
    if os.environ.get("ACCEPT_PIECE_GEOMETRY"):
        sys.stderr.write(f"[accept] piece geometry corpus: {path} "
                         "(ACCEPT_PIECE_GEOMETRY override)\n")
    g = json.loads(Path(path).read_text())
    prefabs = g["prefabs"]
    if not _CORPUS_KEEPS_TRIGGER_VOLUMES:
        if str(JUMPSTART / "blueprints") not in sys.path:
            sys.path.insert(0, str(JUMPSTART / "blueprints"))
        import base_geometry                              # noqa: PLC0415
        phys = base_geometry.Geometry(Path(path))
        prefabs = {nm: ((phys.physical_parts(nm) or [])
                        if nm in phys.trigger_volumes else parts)
                   for nm, parts in prefabs.items()}
    return prefabs, set(g["build_pieces"])


# The corpus as the gate read it until drive 2, restorable by a neuter: every
# non-trigger collider kept, a build piece's 200 m interaction volume
# included.  False is the fix.
_CORPUS_KEEPS_TRIGGER_VOLUMES = False


# The kinds whose row IS the collider.  Anything else is an UPPER BOUND on
# the collider and must never be the last word in a refusal - see
# `Solid.tight_verts` and the spawn section's bounds-only re-run.
TIGHT_KINDS = frozenset(("box", "capsule", "sphere"))
BOUNDS_KIND = "mesh"


def part_kind(part: list) -> str | None:
    """The collider kind of one part row, or None for a pre-2026-09-22 corpus.

    None means UNSTATED, and unstated is not the same claim as `mesh`: a
    caller that needs the precision has to be able to tell "this is a
    bounding box" from "this file predates the question".
    """
    tail = part[-1]
    return tail if isinstance(tail, str) else None


def part_vertices(part: list) -> list[tuple[float, float, float]]:
    kind = part[0]
    # The trailing collider-kind tag is sliced off here and NOWHERE ELSE, so
    # every existing caller of this function keeps reading byte-identical
    # corners out of a corpus that gained a column.
    body = part[1:-1] if isinstance(part[-1], str) else part[1:]
    v = [float(q) for q in body]
    if kind == "b":
        x0, y0, z0, x1, y1, z1 = v
        return [(x, y, z) for x in (x0, x1) for y in (y0, y1)
                for z in (z0, z1)]
    return [(v[i], v[i + 1], v[i + 2]) for i in range(0, len(v), 3)]


def rotate(q: tuple[float, float, float, float],
           p: tuple[float, float, float]) -> tuple[float, float, float]:
    """Unity quaternion rotation, written out rather than imported: the whole
    orientation check is this operation and a dependency would hide it."""
    x, y, z, w = q
    px, py, pz = p
    # t = 2 * cross(q.xyz, p);  p' = p + w*t + cross(q.xyz, t)
    tx = 2.0 * (y * pz - z * py)
    ty = 2.0 * (z * px - x * pz)
    tz = 2.0 * (x * py - y * px)
    return (px + w * tx + (y * tz - z * ty),
            py + w * ty + (z * tx - x * tz),
            pz + w * tz + (x * ty - y * tx))


def _hull_xz(verts) -> list[tuple[float, float]]:
    """Convex hull in xz of a vertex cloud.  One implementation, used for
    the whole solid and for its tight parts alone."""
    pts = sorted({(round(v[0], 4), round(v[2], 4)) for v in verts})
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))
    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]

def _level_top_of(verts, tol: float = 0.05) -> float | None:
    """The y of a top face of ONE vertex cloud flat enough to stand on.

    Split out of `Solid.level_top` so the whole solid and ONE OF ITS PARTS
    are asked the same question by the same code.  `flat enough` is the
    SAME 38 degrees the ground is held to.
    """
    if not verts:
        return None
    top = max(v[1] for v in verts)
    near = [v for v in verts if top - v[1] <= tol]
    if len(near) < 3:
        return None
    span = max(math.hypot(a[0] - b[0], a[2] - b[2])
               for a in near for b in near)
    return None if span < 0.2 else top


def _hull_covers(hull, x: float, z: float, slack: float = 0.0) -> bool:
    """Is (x, z) inside this convex hull, allowing `slack` metres outside?

    `slack` exists because the caller's query point is the point on the
    WHOLE solid's hull nearest the walker, and floating point puts that
    point a hair outside the individual part's own hull it lies on.
    """
    if len(hull) < 3:
        return False
    n = len(hull)
    for i in range(n):
        ax, az = hull[i]
        bx, bz = hull[(i + 1) % n]
        ll = math.hypot(bx - ax, bz - az)
        if ll < 1e-12:
            continue
        side = ((bx - ax) * (z - az) - (bz - az) * (x - ax)) / ll
        if side < -slack - 1e-9:
            return False
    return True


def _upper_plane(verts):
    """`(a, b, c)` of the plane `y = a*x + b*z + c` through the UPPER
    surface of ONE convex part, or None when it is not a surface.

    THE HIGHEST y AT EACH DISTINCT COLUMN, least-squares through those.
    A box's upper points are its four top corners and the fit is exactly
    horizontal, so a floor slab answers what `_level_top_of` answers.  A
    stair's RAMP part is a swept prism - eight points, four high at one
    end and four low at the other - whose upper face is INCLINED, and a
    walker on a stair is standing on exactly that face.

    MEASURED on Ulfsland: `wood_stair` carries two BOX parts (its bottom
    tread and its top tread) and two prism parts (the flights between
    them).  Asked for a LEVEL face the prisms answer None, because their
    top is a line rather than a face - which is true, and which sent the
    reader back to the whole solid's summit.
    """
    top: dict[tuple, float] = {}
    for v in verts:
        k = (round(v[0], 3), round(v[2], 3))
        if k not in top or v[1] > top[k]:
            top[k] = v[1]
    if len(top) < 3:
        return None
    n = float(len(top))
    sx = sz = sy = sxx = szz = sxz = sxy = szy = 0.0
    for (x, z), y in top.items():
        sx += x
        sz += z
        sy += y
        sxx += x * x
        szz += z * z
        sxz += x * z
        sxy += x * y
        szy += z * y
    # 3x3 normal equations, solved by cofactors; a degenerate footprint
    # (a line of columns) has a zero determinant and is not a surface.
    m = ((sxx, sxz, sx), (sxz, szz, sz), (sx, sz, n))
    r = (sxy, szy, sy)

    def det3(mm):
        return (mm[0][0] * (mm[1][1] * mm[2][2] - mm[1][2] * mm[2][1])
                - mm[0][1] * (mm[1][0] * mm[2][2] - mm[1][2] * mm[2][0])
                + mm[0][2] * (mm[1][0] * mm[2][1] - mm[1][1] * mm[2][0]))
    d = det3(m)
    if abs(d) < 1e-9:
        return None
    out = []
    for col in range(3):
        mm = tuple(tuple(r[i] if j == col else m[i][j] for j in range(3))
                   for i in range(3))
        out.append(det3(mm) / d)
    return (out[0], out[1], out[2])


def walkable_surface_of(verts, slide_limit: float = STEP_LIMIT_PER_M):
    """`(plane, gradient)` for a part a walker can stand on, else None.

    A face steeper than the player's own slide limit is not a face he
    stands on - the SAME 38 degrees `_level_top_of` holds a level face to
    and the same one the ground is held to, so a wall's vertical side and
    a roof's pitch are excluded by the same number that admits a stair.
    """
    p = _upper_plane(verts)
    if p is None:
        return None
    grad = math.hypot(p[0], p[1])
    return None if grad > slide_limit else (p, grad)


class Solid:
    """One placed object's solid, in world space."""

    __slots__ = ("name", "pos", "quat", "verts", "ymin", "ymax",
                 "xmin", "xmax", "zmin", "zmax", "known", "_hull",
                 "n_tight", "n_bounds", "_thull")

    def __init__(self, name, pos, quat, parts, known):
        self.name = name
        self.pos = pos
        self.quat = quat
        # `known` is "this prefab is IN the geometry corpus", NOT "this prefab
        # has a solid".  The two are different claims and conflating them is
        # how an unmeasured object becomes a harmless one: `_ZoneCtrl`,
        # `LocationProxy` and `vfx_swamp_mist` are all MEASURED to have no
        # solid at all (`solid_only: true` in piece_geometry.json), while a
        # modded prefab the corpus has never seen is simply unmeasured and
        # must be reported as such rather than silently given zero extent.
        self.known = known
        self._hull = None
        self._thull = None
        # THE TIGHT PARTS COME FIRST, and that ordering is the whole
        # mechanism: `verts[:n_tight]` is the solid AS MEASURED and
        # `verts` is the solid PLUS every MeshCollider's bounding box.  No
        # second vertex list is allocated for 490 k objects to carry a
        # distinction that is a slice.  Nothing downstream depends on the
        # order of `verts` - every consumer takes a min, a max or a convex
        # hull over the whole list.
        tight, loose, self.n_bounds = [], [], 0
        for part in parts or ():
            bucket = (loose if part_kind(part) == BOUNDS_KIND else tight)
            if bucket is loose:
                self.n_bounds += 1
            for v in part_vertices(part):
                r = rotate(quat, v)
                bucket.append((pos[0] + r[0], pos[1] + r[1], pos[2] + r[2]))
        self.n_tight = len(tight)
        vs = tight + loose
        self.verts = vs
        if vs:
            self.xmin = min(v[0] for v in vs)
            self.xmax = max(v[0] for v in vs)
            self.ymin = min(v[1] for v in vs)
            self.ymax = max(v[1] for v in vs)
            self.zmin = min(v[2] for v in vs)
            self.zmax = max(v[2] for v in vs)
        else:
            self.xmin = self.xmax = pos[0]
            self.ymin = self.ymax = pos[1]
            self.zmin = self.zmax = pos[2]

    @property
    def bounds_only(self) -> bool:
        """Is EVERY part of this solid a MeshCollider's bounding box?

        True means the piece has no tight collider anywhere, so its whole
        footprint is an upper bound.  `Beech1` is True and is 19.08 x 21.50 m
        in prefab-local space; `FirTree` is False, because its trunk is a
        0.8 m capsule and only its canopy is bounds.
        """
        return self.n_bounds > 0 and self.n_tight == 0

    def tight_hull(self) -> list[tuple[float, float]]:
        """The footprint of the TIGHT parts alone - the shape as measured,
        with every bounding box dropped.  Empty when there is nothing tight.

        This is the instrument for a re-run: judge again with the bounds
        dropped, and if the verdict flips, the bounding box was deciding it.
        """
        if self._thull is None:
            self._thull = _hull_xz(self.verts[:self.n_tight])
        return self._thull

    def level_top(self, tol: float = 0.05) -> float | None:
        """The y of a top face flat enough to stand on, or None.

        `flat enough` is the SAME 38 degrees the ground is held to: a surface a
        player would slide off is not a surface a player stands on, and using
        one number for both means a bridge deck and the bank it lands on are
        judged by the same rule."""
        return _level_top_of(self.verts, tol)

    def part_faces(self, geom: dict) -> list:
        """Every PART of this solid as `(hull_xz, ymin, walkable_plane)`,
        rotated into world space, computed ON DEMAND from the corpus.

        `walkable_plane` is `(a, b, c)` of `y = a*x + b*z + c` for the
        part's own upper surface when a walker could stand on it, else
        None.  A PLANE AND NOT A NUMBER, because a stair's flight is a
        surface whose height is different at every column and the whole
        question here is which column the walker is in.

        NOT CACHED ON THE INSTANCE AND NOT BUILT IN `__init__`.  There are
        490 k solids in this save and only a few dozen are ever asked to
        name the face a walker arrives on, so per-part geometry is
        recomputed for those few rather than allocated for all of them.
        `Solid.__slots__` exists for the same reason.
        """
        out = []
        for part in geom.get(self.name) or ():
            vs = [(self.pos[0] + r[0], self.pos[1] + r[1], self.pos[2] + r[2])
                  for r in (rotate(self.quat, v)
                            for v in part_vertices(part))]
            if not vs:
                continue
            got = walkable_surface_of(vs)
            out.append((_hull_xz(vs), min(v[1] for v in vs),
                        None if got is None else got[0]))
        return out

    def hull(self) -> list[tuple[float, float]]:
        """The piece's FOOTPRINT, convex hull in xz of its rotated solid.

        An axis-aligned box is not a footprint for anything placed at an
        angle, and a road meets most of its structures at an angle.  Using the
        bounding box here would widen every rotated piece by up to 41 % and
        manufacture obstructions that are not there - the mirror image of the
        coverage test that could not see a flip because it only ever looked at
        bounding boxes.
        """
        if self._hull is None:
            self._hull = _hull_xz(self.verts)
        return self._hull

    def covers(self, x: float, z: float) -> bool:
        h = self.hull()
        if len(h) < 3:
            return False
        for i in range(len(h)):
            a, b = h[i], h[(i + 1) % len(h)]
            if ((b[0] - a[0]) * (z - a[1])
                    - (b[1] - a[1]) * (x - a[0])) < -1e-9:
                return False
        return True

    def lateral_span(self, x: float, z: float, ux: float, uz: float,
                     limit: float) -> tuple[float, float] | None:
        """Where this piece crosses the carriageway's cross-section line.

        The station's cross-section is the segment from -limit to +limit along
        (ux,uz); this clips it against the piece's own footprint.  A wall that
        crosses the road diagonally has no vertex anywhere near the station
        and would be invisible to a vertex test, which is exactly the class of
        miss this gate exists to stop.
        """
        h = self.hull()
        if len(h) < 3:
            d = (self.pos[0] - x) * ux + (self.pos[2] - z) * uz
            return (d, d) if abs(d) <= limit else None
        lo, hi = -limit, limit
        for i in range(len(h)):
            a, b = h[i], h[(i + 1) % len(h)]
            # inward half-plane: cross(b-a, p-a) >= 0 for a CCW hull
            nx, nz = -(b[1] - a[1]), (b[0] - a[0])
            c = nx * (x - a[0]) + nz * (z - a[1])
            den = nx * ux + nz * uz
            if abs(den) < 1e-12:
                if c < -1e-9:
                    return None
                continue
            t = -c / den
            if den > 0:
                lo = max(lo, t)
            else:
                hi = min(hi, t)
            if lo > hi:
                return None
        return (lo, hi)


# ---------------------------------------------------------------------------
# WHERE A WALKER ARRIVES ON A PIECE, AND WHAT HE IS STANDING ON THERE
# ---------------------------------------------------------------------------
# `Solid.level_top()` answers ONE number for a whole piece: its highest face
# flat enough to stand on.  That is the right answer for a floor slab, whose
# only face IS its top, and the WRONG answer for anything that SPANS HEIGHT.
#
# MEASURED on Ulfsland, frozen save 9a242873, and it is the same inversion
# `_nearest_standing` already refuses for an arch's crown - only this time
# the piece IS the right destination and only the FACE is wrong:
#
#   road_R1_ulfsgard_t8junction A (1007.5, 721.5)  applied 30.528
#     `wood_stair` at (1022, 712), span y 30.430..31.551.  `level_top` is
#     31.551, THE TOP TREAD - a 0.098 m deep ledge at the far end of the
#     flight - and the terminus was refused by -1.023 m.  The tread the
#     walker actually meets is the BOTTOM one at 30.599, and against that
#     the road is -0.071 m: INSIDE the 0.245 m tolerance.
#   road_T13_temple_harboursouth B (12.0, -258.0)  applied 31.195
#     `wood_stair` span 31.107..32.227, refused by -1.032 m against the top
#     tread 32.227; its bottom tread is 31.276 and the road is -0.081 m.
#
# Both roads were built correctly.  A gate that reads a staircase's summit
# as "the height of the thing this road meets" refuses the road for being
# at the bottom of a staircase, which is where a staircase is climbed from.
#
# So: the face is read AT THE COLUMN THE WALKER ARRIVES IN, per PART, and
# the arrival column is the point of the piece's own footprint nearest him.
def hull_point(hull, px: float, pz: float) -> tuple[float, float]:
    """The point of this convex footprint nearest (px, pz); the query point
    itself when it is already inside."""
    if len(hull) < 3:
        return (px, pz) if not hull else min(
            hull, key=lambda a: math.hypot(px - a[0], pz - a[1]))
    if _hull_covers(hull, px, pz):
        return (px, pz)
    best, bd = (px, pz), math.inf
    n = len(hull)
    for i in range(n):
        ax, az = hull[i]
        bx, bz = hull[(i + 1) % n]
        vx, vz = bx - ax, bz - az
        ll = vx * vx + vz * vz
        t = 0.0 if ll == 0 else max(0.0, min(
            1.0, ((px - ax) * vx + (pz - az) * vz) / ll))
        cx, cz = ax + t * vx, az + t * vz
        d = math.hypot(px - cx, pz - cz)
        if d < bd:
            best, bd = (cx, cz), d
    return best


def arrival_face(solid, geom: dict, px: float, pz: float,
                 tol: float = 0.05, slack: float = 0.05
                 ) -> tuple[float | None, float, float]:
    """`(walkable y, arrival x, arrival z)` for a walker coming from
    (px, pz) onto this piece.

    The arrival column is the nearest point of the piece's own footprint.
    The height is the highest WALKABLE PART SURFACE over that column,
    each part's own upper plane evaluated there - so a floor slab answers
    exactly what `level_top()` answered, and a stair answers the height
    of the flight AT THE WALKER'S FEET rather than at its summit.

    Falls back to the whole-solid `level_top()` when no part covers the
    column: that is the pre-2026-09-22 answer, and an unmeasurable face
    must not silently become no destination at all.
    """
    ax, az = hull_point(solid.hull(), px, pz)
    best = None
    for phull, _pymin, plane in solid.part_faces(geom):
        if plane is None or not _hull_covers(phull, ax, az, slack):
            continue
        y = plane[0] * ax + plane[1] * az + plane[2]
        if best is None or y > best:
            best = y
    return (solid.level_top(tol) if best is None else best), ax, az


def solids_from_census(cen: Census, geom: dict[str, list]) -> list[Solid]:
    out = []
    for name, x, y, z, qx, qy, qz, qw in cen.rows:
        parts = geom.get(name)
        out.append(Solid(name, (x, y, z), (qx, qy, qz, qw), parts or [],
                         parts is not None))
    return out


# ---------------------------------------------------------------------------
# a coarse spatial index, because 490 k objects x 22 k stations is not a loop
# ---------------------------------------------------------------------------
class Grid:
    def __init__(self, solids: list[Solid], cell: float = 8.0):
        self.cell = cell
        self.buckets: dict[tuple[int, int], list[Solid]] = defaultdict(list)
        for s in solids:
            for ix in range(int(math.floor(s.xmin / cell)),
                            int(math.floor(s.xmax / cell)) + 1):
                for iz in range(int(math.floor(s.zmin / cell)),
                                int(math.floor(s.zmax / cell)) + 1):
                    self.buckets[(ix, iz)].append(s)

    def near(self, x: float, z: float, r: float) -> list[Solid]:
        """Every solid whose bounding box touches a cell in range, ONCE.

        A solid spans as many cells as it is wide, so a naive concatenation
        returns the same wall four times and a four-piece obstruction becomes
        sixteen.  Counting the same object more than once is how "184 objects"
        happens; the de-duplication is not an optimisation."""
        c = self.cell
        seen: dict[int, Solid] = {}
        for ix in range(int(math.floor((x - r) / c)),
                        int(math.floor((x + r) / c)) + 1):
            for iz in range(int(math.floor((z - r) / c)),
                            int(math.floor((z + r) / c)) + 1):
                for s in self.buckets.get((ix, iz), ()):
                    seen[id(s)] = s
        return list(seen.values())


# ---------------------------------------------------------------------------
# CLASSIFICATION - stated in the open, never inside an instrument
# ---------------------------------------------------------------------------
def load_clearable() -> tuple[set[str], set[str]]:
    sys.path.insert(0, str(JUMPSTART / "roads"))
    import clear as C  # noqa: PLC0415
    return set(C.CLEARABLE), set(C.NOT_PROPERTY)


# ---------------------------------------------------------------------------
# the walking surface, and the steps between them
# ---------------------------------------------------------------------------
def surfaces_at(x: float, z: float, bed: float, grid: Grid,
                walker_r: float) -> list[tuple[float, Solid | None]]:
    """Every height a player's feet can rest on at this xz.

    The ground, plus the level top of any solid whose footprint covers the
    point.  THIS IS HOW A BRIDGE STOPS BEING A DEFECT: a deck at its own datum
    over a bed 3 m below is two surfaces at the same xz, the walk uses the one
    it can reach from the abutment, and no span has to be named or excluded by
    hand.  A roof 4 m over the road is also a surface here and is simply never
    reachable, which is the correct answer for a roof."""
    out: list[tuple[float, Solid | None]] = [(bed, None)]
    for s in grid.near(x, z, 0.0):
        if not (s.xmin - 1e-6 <= x <= s.xmax + 1e-6
                and s.zmin - 1e-6 <= z <= s.zmax + 1e-6):
            continue
        t = s.level_top()
        if t is None or t < bed - 0.01 or not s.covers(x, z):
            continue
        out.append((t, s))
    out.sort(key=lambda t: t[0])
    return out


def blockers_at(x: float, z: float, surf: float, ux: float, uz: float,
                half_width: float, grid: Grid, clearable: set[str],
                not_property: set[str]) -> list[tuple[float, float, Solid]]:
    """Lateral intervals of the carriageway a walker cannot walk through.

    A piece counts only if it rises out of the walking surface by more than the
    SAME limit the ground is held to (0.7813 m): below that it is a step of the
    size terrain is allowed to take anyway, and calling it a wall would invent
    a step-height rule the game does not have.  A piece whose underside is
    already above that is a gantry and is walked under.
    """
    out = []
    for s in grid.near(x, z, half_width + 1.0):
        if s.name in clearable or s.name in not_property:
            continue
        if not s.known:
            # UNMEASURABLE.  The prefab is not in the geometry corpus, so
            # this object has no extent anyone has measured.  It is placed on
            # the list at its origin and REPORTED as unknown, because the one
            # answer a gate may never give is "I could not measure it, so it
            # is fine" -- that is the shape of every defect this file exists
            # to catch.
            d = (s.pos[0] - x) * ux + (s.pos[2] - z) * uz
            lon = (s.pos[0] - x) * -uz + (s.pos[2] - z) * ux
            if abs(d) <= half_width and abs(lon) <= 0.5:
                out.append((max(d - 0.25, -half_width),
                            min(d + 0.25, half_width), s))
            continue
        if not s.verts:
            continue                    # MEASURED to have no solid at all
        if s.ymax <= surf + STEP_LIMIT_PER_M:
            continue                    # a step no bigger than terrain's own
        if s.ymin > surf + STEP_LIMIT_PER_M:
            continue                    # a gantry: walked under
        span = s.lateral_span(x, z, ux, uz, half_width)
        if span is None:
            continue
        out.append((span[0], span[1], s))
    return out


def widest_gap(intervals: list[tuple[float, float]], half: float) -> float:
    if not intervals:
        return 2 * half
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    best = merged[0][0] - (-half)
    for i in range(len(merged) - 1):
        best = max(best, merged[i + 1][0] - merged[i][1])
    best = max(best, half - merged[-1][1])
    return best


# ---------------------------------------------------------------------------
# bodies - connected clusters of built pieces
# ---------------------------------------------------------------------------
def bodies(solids: list[Solid], slack: float = 0.35) -> list[list[Solid]]:
    idx = {id(s): i for i, s in enumerate(solids)}
    parent = list(range(len(solids)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    grid = Grid(solids, cell=4.0)
    for s in solids:
        i = idx[id(s)]
        for t in grid.near((s.xmin + s.xmax) / 2, (s.zmin + s.zmax) / 2,
                           max(s.xmax - s.xmin, s.zmax - s.zmin) / 2 + slack):
            if t is s:
                continue
            if (s.xmin - slack <= t.xmax and t.xmin - slack <= s.xmax
                    and s.zmin - slack <= t.zmax and t.zmin - slack <= s.zmax
                    and s.ymin - slack <= t.ymax and t.ymin - slack <= s.ymax):
                union(i, idx[id(t)])
    groups: dict[int, list[Solid]] = defaultdict(list)
    for s in solids:
        groups[find(idx[id(s)])].append(s)
    return list(groups.values())


# ---------------------------------------------------------------------------
# ORIENTATION, as a SIGNED quantity - a WORLD property, not a road one
# ---------------------------------------------------------------------------
# This is the instrument, and it lives here rather than in a section because
# 385 inverted panels in 74 bodies are spread over the whole world and only a
# handful are anywhere near a road.  The `structures` section is its only
# caller and its only reporter.
def rise_vector(parts: list, quat) -> tuple[tuple[float, float, float],
                                            float] | None:
    """World vector from a piece's LOW edge to its HIGH edge, and its own local
    pitch.  `None` for a piece with no slope, which is most of them."""
    vs = []
    for part in parts:
        vs.extend(part_vertices(part))
    if len(vs) < 4:
        return None
    ylo, yhi = min(v[1] for v in vs), max(v[1] for v in vs)
    if yhi - ylo < 0.15:
        return None
    hi = [v for v in vs if yhi - v[1] <= 0.05 * (yhi - ylo) + 1e-6]
    lo = [v for v in vs if v[1] - ylo <= 0.05 * (yhi - ylo) + 1e-6]
    if not hi or not lo:
        return None
    hc = tuple(sum(v[i] for v in hi) / len(hi) for i in range(3))
    lc = tuple(sum(v[i] for v in lo) / len(lo) for i in range(3))
    d = (hc[0] - lc[0], hc[1] - lc[1], hc[2] - lc[2])
    if math.hypot(d[0], d[2]) < 0.15:
        return None                     # a vertical piece has no ridge sense
    return rotate(quat, d), math.hypot(d[0], d[2])


# HOW MUCH CLOSER TO THE RIDGE A PANEL'S HIGH EDGE MUST BE THAN ITS LOW EDGE
# BEFORE THE SIGN MEANS ANYTHING.  MEASURED on Ulfsland save 5385652e, over
# every roof body in the world (565 panels whose high edge is the farther of
# the two):
#
#     margin  < 0.01 m   382 panels   a panel exactly BETWEEN two ridges
#     0.01 - 0.10 m       18 panels   the empty band
#     > 0.10 m           165 panels   the signal
#
# The spike under 0.01 m is symmetry, not a defect: a panel on the shared
# eave of two halls is the same distance from both ridges at both of its
# edges, and which one is "nearer" is then decided by float dust.  MEASURED
# at harbour-city-ulfsgard: 14 CORRECT panels on the shared eave at x=1080.5
# tie at 0.006 m and the old rule refused all 14 of them, while the 52
# genuinely reversed panels of the same roof separate at 0.506 m and every
# sound panel of that roof separates at 2.000 m - a full panel width.  So
# the limit is set inside the measured empty band and not at zero.
ORIENT_MARGIN_M = 0.05


def rise_edges(parts: list, quat, pos) -> tuple[tuple[float, float, float],
                                                tuple[float, float, float]] \
        | None:
    """A piece's LOW and HIGH edge centres, placed IN THE WORLD.

    `rise_vector` answers "which way does this piece climb" as a direction
    with no anchor.  A direction has to be compared with a SECOND direction -
    "and where is the ridge from here" - and that second one is what the
    old orientation rule could not compute without choosing one ridge panel
    out of several, by a distance that ties whenever the roof is symmetric.
    Two ANCHORED points need no such choice: each is asked the same question
    of the same ridge set, and a tie is then a genuine tie rather than a
    coin toss.  `None` for a piece with no slope, which is most of them.
    """
    vs = []
    for part in parts:
        vs.extend(part_vertices(part))
    if len(vs) < 4:
        return None
    ylo, yhi = min(v[1] for v in vs), max(v[1] for v in vs)
    if yhi - ylo < 0.15:
        return None
    hi = [v for v in vs if yhi - v[1] <= 0.05 * (yhi - ylo) + 1e-6]
    lo = [v for v in vs if v[1] - ylo <= 0.05 * (yhi - ylo) + 1e-6]
    if not hi or not lo:
        return None
    hc = tuple(sum(v[i] for v in hi) / len(hi) for i in range(3))
    lc = tuple(sum(v[i] for v in lo) / len(lo) for i in range(3))
    if math.hypot(hc[0] - lc[0], hc[2] - lc[2]) < 0.15:
        return None                     # a vertical piece has no ridge sense
    hw, lw = rotate(quat, hc), rotate(quat, lc)
    return ((pos[0] + hw[0], pos[1] + hw[1], pos[2] + hw[2]),
            (pos[0] + lw[0], pos[1] + lw[1], pos[2] + lw[2]))


# THE RIDGE NOMINATES; THE PIECE'S OWN SEAMS DECIDE.
#
# "Rises toward its body's ridge" is a PROXY for what an inverted panel is -
# a piece whose edges do not meet the roof around them - and the proxy
# needs a ridge.  Every way of choosing one has now failed on a real body,
# each time by judging a correct piece against a ridge that is not its own:
#
#   the nearest ridge point      14 correct eave panels at harbour-city-
#                                ulfsgard, over 6 mm (see `check_orientation`)
#   the body's highest top       blackforest-walled-base (`fox-0123456`):
#                                35 correct panels of LOWER WINGS (ridges
#                                43.49-50.31 m) judged against the tallest
#                                one.  The emitter yawed all 35 and the gate
#                                then passed the roof it had broken: roof_skin
#                                3 holes / 12 spanned steps as authored, 5 / 31
#                                as emitted (MEASURED offline, drive 2).
#   a ridge piece as its own     sandbox-portal-hub (`salty-dick-portal-hub-
#   reference                    final`): its four `darkwood_roof_ocorner`
#                                caps ARE the ridge set, and a cap's own box
#                                centre sits 0.56 m from its low edge and
#                                1.50 m from its apex, so every cap read
#                                -0.947 m authored and -0.947 m turned 180 -
#                                no pose of it could pass, and the refusal
#                                stranded every portal pair in the hub.
#
# A per-building partition (`structures.split_by_building`) cures the first
# two only where the wings are separate buildings; a blueprint's wings are
# one.  So a nominated piece is asked the question directly: DO ITS OWN
# EDGES MEET THE ROOF AROUND IT BETTER AS PLACED THAN TURNED 180?  Just past
# each edge (SEAM_PROBE_M, along its own rise) the other roof pieces of its
# body are cut by a vertical line (`structures.Columns.span`, the section's
# one column primitive) and the nearest upper surface to that edge's own
# height is its seam mismatch; an edge with nothing past it (an eave, an
# apex) says nothing.  A correct panel meets the course above at its high
# edge and the course below at its low edge; turned, each edge faces
# material a whole course away.  MEASURED, placed vs turned, mean mismatch
# per seen edge: the hub caps 0.042 vs 1.105 m, blackforest's wing panels
# 0.11-0.25 vs 1.1-2.4 m.  The REAL inversions go the other way and are
# never cleared: the meadows gable triangles (hs_meadows_cottage / _hut /
# _stilt_house / _stone_house / _stone_manor, the counts the gate found in
# the world) -1.25 to -2.25 m, hs_swamp_vrolok_caba_a_2's three
# `wood_roof_45` -1.27 to -2.12 m.
#
# THE MARGIN IS THE PROBE'S OWN RESOLUTION.  A seam read SEAM_PROBE_M past
# an edge sees the neighbour's surface displaced by up to its slope over
# that distance, so a difference smaller than `SEAM_PROBE_M x the piece's
# own pitch` is slope, not fit, and clears nothing.  It only ever CLEARS: a
# piece the ridge rule passes is not re-judged here, so no body can gain a
# refusal from it.  A GUTTER - an apex course laid backwards on both sides,
# each panel consistent with its reversed partner - reads worse placed than
# turned (the cliff at its outer edge), is not cleared, and is still refused
# by the ridge rule that exists to see it.
SEAM_PROBE_M = 0.3
# The instrument as it shipped, restorable by a neuter: the ridge proxy
# alone decides.  False is the fix.
_RIDGE_ALONE_DECIDES = False

# A PIECE ROLLED ONTO ITS SIDE IS NOT IN A ROOF PLANE.  Valheim's own build
# tool only ever yaws a piece; a blueprint can roll one, and a roof-class
# piece rolled 90 degrees is a wall, a bracket or a trim.  Its own UP axis
# is then horizontal, so "its high edge is placed below its low edge" is
# decided by the SIGN of the roll - +90 and -90 are the same corbel,
# mirrored - and "which way does it climb to the ridge" by the same coin.
# MEASURED on `ashlands-forward-base` (`hs_ashlands_compact_endgame_base1`):
# the four `ashwood_wall_roof_26[_upsidedown]` rows 43-46 are two mirrored
# pairs of corbels on the pillars at local (0.07, 3.53) and (-3.26, 1.31)
# under the floor at 5.15 m, every one with its own up at y = -0.0007 - on
# its side, not face-down.  The emitter reposed three into their mirror
# twins and refused the body for the fourth, whose twin "rises away" from
# a ridge 2-4 m above a pillar bracket: 0.674 m.  A piece whose own up is
# within ON_SIDE_TOL of horizontal is counted `on_side` and not judged.
# The tolerance is a 90-degree roll written as a 3-4 decimal quaternion
# (|up.y| up to ~2e-3), and 0.01 is 0.57 degrees: a piece tilted by any
# deliberate amount short of on its side is still asked both questions.
ON_SIDE_TOL = 0.01
# The shipped population, restorable by a neuter: every sloped roof-class
# piece is judged whatever its roll.  False is the fix.
_ON_SIDE_JUDGED = False


def qmul(a, b) -> tuple[float, float, float, float]:
    """Hamilton product, xyzw - `a` applied after `b`."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def seam_mismatch(s, geom: dict[str, list], cols, grid, own=None
                  ) -> tuple[float, int] | None:
    """`(mean mismatch over the edges that meet material, how many did)`
    for this piece AS POSED - see `SEAM_PROBE_M`.  `grid` holds the roof
    pieces it may meet; `own` (default `s`) is the placed solid this pose
    stands in for, which is never its own neighbour.  None for a piece with
    no slope."""
    own = s if own is None else own
    e = rise_edges(geom.get(s.name) or [], s.quat, s.pos)
    if e is None:
        return None
    (hx, hy, hz), (lx, ly, lz) = e
    run = math.hypot(hx - lx, hz - lz)
    ux, uz = (hx - lx) / run, (hz - lz) / run
    total, seen = 0.0, 0
    for (px, py, pz), sign in (((hx, hy, hz), 1.0), ((lx, ly, lz), -1.0)):
        qx, qz = px + sign * ux * SEAM_PROBE_M, pz + sign * uz * SEAM_PROBE_M
        best = None
        for t in grid.near(qx, qz, 0.0):
            if t is own:
                continue
            sp = cols.span(t, qx, qz)
            if sp is not None and (best is None or abs(sp[1] - py) < best):
                best = abs(sp[1] - py)
        if best is not None:
            total += best
            seen += 1
    return (total / seen if seen else 0.0), seen


def seam_clears(s, geom: dict[str, list], cols, grid) -> dict | None:
    """The numbers that CLEAR a nominated piece, or None: its own edges
    meet the roof around it better as placed than turned 180 about its own
    pivot, by more than its own pitch over the probe."""
    e = rise_edges(geom.get(s.name) or [], s.quat, s.pos)
    if e is None:
        return None
    placed = seam_mismatch(s, geom, cols, grid)
    turned = seam_mismatch(Solid(s.name, s.pos, qmul(yaw(180.0), s.quat),
                                 geom.get(s.name) or [], s.known),
                           geom, cols, grid, own=s)
    if not (placed and turned and placed[1] and turned[1]):
        return None
    (hx, hy, hz), (lx, ly, lz) = e
    need = SEAM_PROBE_M * abs(hy - ly) / math.hypot(hx - lx, hz - lz)
    if turned[0] - placed[0] <= need:
        return None
    return dict(placed_m=round(placed[0], 4), placed_edges=placed[1],
                turned_m=round(turned[0], 4), turned_edges=turned[1],
                need_m=round(need, 4))


def check_orientation(solids: list[Solid], geom: dict[str, list]) -> dict:
    """For every ORIENTED class, does each piece rise toward its body's ridge?

    THE SIGN IS THE WHOLE POINT.  A count of roof panels, a census of their
    positions and an axis-aligned bounding-box overlap are all invariant under
    the 180-degree yaw that inverts a roof, which is how 480 inverted panels
    were certified "349/349 at tolerance 0".  The discriminator here is the
    one quantity that flip changes: IS THE PANEL'S HIGH EDGE NEARER ITS
    BODY'S RIDGE THAN ITS LOW EDGE?

    WHY IT IS AN EDGE COMPARISON AND NOT `dot(rise, to-ridge)` ANY MORE.  The
    dot product needs ONE ridge point, and the only rule available for
    picking it was "the nearest".  MEASURED at harbour-city-ulfsgard, two
    gabled halls sharing an eave at x=1080.5, ridges 12 m apart at x=1074.5
    and x=1086.5:

      the 14 eave panels sit 6.000 m from one ridge and 6.006 m from the
      other, so `min()` handed 13 of them the ridge of the OTHER hall and
      the check refused 14 CORRECT PANELS over 6 MILLIMETRES;

      and the 52 panels of that roof that ARE reversed - its whole apex
      course, which makes a 0.97 m gutter where the ridge should be and
      104 daylight steps in `roof_skin` - WERE NEVER JUDGED AT ALL, because
      a panel at the apex is its own nearest ridge point and the old rule
      skipped anything closer than 0.75 m to the ridge.

    Both halves of that are gone here: no ridge point is chosen, so there is
    no tie to break, and no panel is skipped for being near the ridge, so
    the apex course is judged like everything else.  MEASURED, same save,
    same bodies, nothing moved: the old rule said 14 inverted in this run's
    bodies and 57 world-wide; this one says 52 and 168, and on the state
    BEFORE ledger seq 5074 repaired 144 of this roof's 196 panels it says
    182 of 196 where the old rule could only reach 144 of 144 it checked.

    A BODY WHOSE EVERY PANEL TOPS OUT AT THE SAME HEIGHT HAS NO RIDGE, and
    is reported UNMEASURED rather than judged.  A single-pitch lean-to, or
    one course of a hip laid flat, has nothing to climb toward; asking it to
    would make every panel on the OUTSIDE of the group read as inverted.
    MEASURED: 3 of this run's 53 roof bodies, 42 panels, and 113 of the
    world's 486, are that shape.
    """
    roofs = [s for s in solids
             if ("roof" in s.name.lower() or "_shingle" in s.name.lower())
             and s.known]
    out = dict(instrument="save ZDO census, rotation applied to each prefab's "
                          "own geometry from blueprints/data/piece_geometry"
                          ".json",
               population="every placed piece whose prefab name contains "
                          "'roof'",
               pieces=len(roofs), bodies=0, checked=0, inverted=0,
               upside_down=0, worst=[], no_ridge_bodies=0, no_ridge_panels=0,
               margin_limit_m=ORIENT_MARGIN_M, inverted_at=[],
               seam_cleared=0, seam_cleared_at=[],
               seam_probe_m=None if _RIDGE_ALONE_DECIDES else SEAM_PROBE_M,
               on_side=0)
    if not roofs:
        return out
    worst = []
    bad_bodies = []
    inverted_at = []
    cleared_at = []
    for body in bodies(roofs, slack=1.2):
        out["bodies"] += 1
        if len(body) < 3:
            continue
        # THE RIDGE IS A SET OF POINTS, AND EVERY EDGE IS ASKED ABOUT ALL OF
        # THEM.  It is every panel within 0.5 m of the body's own highest
        # top - a long hall's ridge is a line, not its single highest panel -
        # and the reference for a given panel is `min over the whole set`,
        # asked identically of its low edge and of its high edge.  Because
        # the SAME set and the SAME reduction answer both, an L-shaped body
        # merged out of two wings, or two halls sharing an eave, no longer
        # turns on which wing's ridge happened to win a distance tie: a
        # panel equidistant from two ridges reports a margin of zero and is
        # left alone, which is the honest answer for a piece that could
        # belong to either.
        top = max(s.ymax for s in body)
        rpieces = [s for s in body if top - s.ymax <= 0.5]
        tops = sorted({round(s.ymax, 2) for s in body})
        if len(rpieces) == len(body):
            out["no_ridge_bodies"] += 1
            out["no_ridge_panels"] += len(body)
            continue
        ridge = [((s.xmin + s.xmax) / 2, (s.zmin + s.zmax) / 2)
                 for s in rpieces]
        bad = 0
        checked_here = 0
        seams = None          # (Columns, Grid) of this body, built on demand
        for s in body:
            e = rise_edges(geom.get(s.name) or [], s.quat, s.pos)
            if e is None:
                continue
            if (not _ON_SIDE_JUDGED and abs(rotate(s.quat, (0.0, 1.0, 0.0))[1])
                    <= ON_SIDE_TOL):
                out["on_side"] += 1          # see ON_SIDE_TOL
                continue
            (hx, hy, hz), (lx, ly, lz) = e
            dh = min(math.hypot(p[0] - hx, p[1] - hz) for p in ridge)
            dl = min(math.hypot(p[0] - lx, p[1] - lz) for p in ridge)
            out["checked"] += 1
            checked_here += 1
            if hy < ly:
                out["upside_down"] += 1
            toward = dl - dh          # + climbs toward the ridge, - away
            if toward < -ORIENT_MARGIN_M:
                # NOMINATED, NOT YET JUDGED: see `SEAM_PROBE_M`.
                if not _RIDGE_ALONE_DECIDES:
                    if seams is None:
                        from .sections.structures import Columns  # noqa
                        seams = (Columns(geom), Grid(body, cell=4.0))
                    c = seam_clears(s, geom, *seams)
                    if c is not None:
                        out["seam_cleared"] += 1
                        cleared_at.append(dict(
                            prefab=s.name,
                            pos=[round(s.pos[0], 4), round(s.pos[1], 4),
                                 round(s.pos[2], 4)],
                            margin_m=round(toward, 4), **c))
                        continue
                out["inverted"] += 1
                bad += 1
                worst.append((round(toward, 3), s.name,
                              round(s.pos[0], 1), round(s.pos[1], 2),
                              round(s.pos[2], 1)))
                # THE REPAIR'S POPULATION IS THIS LIST AND NOTHING ELSE.
                # Two repair tools used to re-derive it - `preflight
                # .upright_plan` and `repose.inverted_panels` - and a repair
                # that derives its own population answers a different
                # question from the check that will judge it.  MEASURED when
                # that last happened: a plan of 13 rows cured ONE panel of
                # 17 and declared itself finished, and on a CLEAN roof the
                # same code proposed three correct panels for a 180 degree
                # flip.  So the rows are published here, keyed by prefab and
                # FULL 3D POSITION because ZDO ids are reassigned on load.
                inverted_at.append(dict(
                    prefab=s.name,
                    pos=[round(s.pos[0], 4), round(s.pos[1], 4),
                         round(s.pos[2], 4)],
                    margin_m=round(toward, 4),
                    upside_down=hy < ly,
                    panels=len(body),
                    body=[round(sum(t.pos[0] for t in body) / len(body), 1),
                          round(sum(t.pos[2] for t in body) / len(body), 1)]))
        if bad:
            bad_bodies.append(dict(
                inverted=bad, checked=checked_here, panels=len(body),
                at=[round(sum(s.pos[0] for s in body) / len(body), 1),
                    round(sum(s.pos[2] for s in body) / len(body), 1)],
                # THE BODY'S OWN GEOMETRY, so the finding cannot be
                # re-litigated: `tops` distinguishes courses of one pitch
                # (spaced by the prefab's own rise) from two ridges at two
                # heights, which is the first hypothesis anyone reaching for
                # this finding will try.
                ridge_y=round(top, 3), ridge_panels=len(ridge),
                panel_tops=tops[:12],
                panel_top_steps=[round(b - a, 3)
                                 for a, b in zip(tops, tops[1:])][:11]))
    worst.sort()
    bad_bodies.sort(key=lambda b: -b["inverted"])
    inverted_at.sort(key=lambda r: r["margin_m"])
    out["worst"] = worst[:20]
    out["inverted_at"] = inverted_at
    out["seam_cleared_at"] = cleared_at
    out["inverted_bodies"] = bad_bodies[:25]
    out["bodies_with_inversions"] = len(bad_bodies)
    out["pass"] = out["inverted"] == 0 and out["upside_down"] == 0
    return out


# ---------------------------------------------------------------------------
# FIXTURES - so no section hand-rolls a second one
# ---------------------------------------------------------------------------
def mk_solid(name: str, pos, quat, parts) -> Solid:
    """A placed solid built in code, for a selftest case.  `known=True`:
    a fixture whose geometry the case itself supplies is measured BY the
    case."""
    return Solid(name, pos, quat, parts, True)


def yaw(deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2.0
    return (0.0, math.sin(h), 0.0, math.cos(h))


# ---------------------------------------------------------------------------
# THE REGRADE CENSUS - may this terrain write bury or undermine anything?
# ---------------------------------------------------------------------------
# ONE IMPLEMENTATION, BECAUSE THREE AGENTS HAND-ROLLED IT IN ONE EVENING AND
# ALL THREE GOT IT WRONG, IN THREE DIFFERENT DIRECTIONS.  It is the question
# asked before every terrain write on this world and it had no instrument, so
# each caller wrote it again from the last person's summary.
#
#   pivots instead of solids      a pivot census of one terrace answered
#                                 "2 ZDOs in the rectangle, 0 buried"; the
#                                 footprint census answered FOURTEEN.  A
#                                 PIVOT IS NOT A SOLID.
#   every solid instead of the    a census over all 488,980 ZDOs refused a
#   build pieces                  clean pad because `Beech1` - A TREE - would
#                                 be "undermined by 22.071 m", span
#                                 70.021..73.536 over ground 48.275.  A
#                                 tree's corpus solid is not ground-relative
#                                 and a tree is not what this asks about.
#   the whole rectangle instead   a terrace was WITHDRAWN over two pieces -
#   of the samples that move      wood_roof foot 36.225, wood_wall_roof foot
#                                 36.005 - that stand over no moving sample
#                                 and were never ground-bearing: they are
#                                 1.6-1.9 m up, carried by the hut's walls.
#                                 "A cut is refused by any solid whose span
#                                 bottom is above the target" is satisfied by
#                                 EVERY piece above ground level in the
#                                 world, and it refused a clean terrace.
#
# And one more, from the same evening, in a different check: a probe that
# reported "28 of 28 lamps sit inside a build-piece span" where the piece WAS
# the lamp.  The subject must be excluded or the probe answers with itself.
#
# So the five rules below each exist because somebody violated one tonight,
# and they are here rather than in five callers' heads.
REGRADE_MOVE_M = 0.01     # `applied.AUTHOR_TOLERANCE_M`: below this the
                          # ground does not move, so nothing standing on it
                          # can be buried or undermined by the write
REGRADE_BEARING_M = 0.05  # `structures.BURIED_TOL_M`: how close a solid's
                          # own span bottom must be to the CURRENT surface
                          # for the ground to be what is holding it up
REGRADE_BURIAL_IS_A_TRANSITION = True  # rule 6: a piece whose top is already
                          # under the present ground is not buried BY this
                          # write; the neuter surface for that rule


def regrade_census(rect, target, ground: dict, solids, geom,
                   columns, gkey, piece_columns, exclude=()) -> dict:
    """Would this write bury or undermine a standing piece?

    `target` IS A SURFACE, NOT A DATUM, and that generalisation is the whole
    reason this signature moved.  A PAD levels its rectangle to ONE height and
    a float says so; A ROAD stamps a DIFFERENT height at every lattice sample
    and has no datum to name.  MEASURED 2026-09-22: every settlement write in
    this world has been through this census and NOT ONE ROAD WRITE EVER HAS,
    because the only shape this took was the pad's.  A question that can only
    be asked of half the writes is not a gate, and `ribbon.py` moves more
    ground in one segment than a night of pads.

    So `target` is EITHER a float (the pad case, unchanged to the byte) OR a
    mapping `(x, z) -> height` (the ribbon case: `taken_after`, the applied
    height this write leaves at each sample it authors).  A sample of `rect`
    the mapping does not name is one THIS WRITE DOES NOT WRITE: it is skipped
    and COUNTED in `samples_without_a_target`, never silently treated as
    unmoved -- absent is not empty, and a rect inflated past the written set
    would otherwise read as a clean census over ground nobody stamped.

    THE FIVE RULES, in order, each one a defect somebody shipped tonight:

      1. THE POPULATION IS THE SAMPLES THAT MOVE.  A lattice sample already
         at target does not move the ground, so nothing standing on it can be
         affected by the write.  Judged at `REGRADE_MOVE_M`.
      2. GROUND-BEARING ONLY.  A solid enters the question only if its own
         span BOTTOM sits within `REGRADE_BEARING_M` of the CURRENT applied
         surface under it.  A roof carried by walls is not undermined by a
         cut, and asking otherwise refuses every piece above ground level.
      3. THEN THE SIGN DECIDES, and both signs are dangerous:
           a FILL buries a solid whose span TOP the new ground reaches
             (target > top - REGRADE_BEARING_M);
           a CUT UNDERMINES one whose foot the new ground leaves behind by
             more than the walker's own step limit.
         "A cut cannot bury anything, so it is safe" is true and insufficient.
      4. EXCLUDE THE SUBJECT - by prefab AND full 3D position, the same key
         the retires use - or the probe answers with itself.
      5. BUILD PIECES ONLY: `solids` must already be that population.

    Every geometric question goes through the caller's own `columns.span` and
    `piece_columns`, which are the pair `check_grounding` uses, so this and
    the gate cannot disagree about what burial is.
    """
    skip = {(s.name, tuple(round(v, 3) for v in s.pos)) for s in exclude}
    # ONE RESOLVER, so the pad case and the ribbon case cannot drift apart.
    # A float is a constant surface; a mapping is the surface itself, keyed
    # by the same `(float(x), float(z))` tuple `moving` and `piece_columns`
    # agree on, because a key two readers round differently is two answers.
    if isinstance(target, dict):
        tmap = {(float(x), float(z)): float(v) for (x, z), v in target.items()}
        target_kind = f"per-sample surface over {len(tmap)} written sample(s)"

        def target_at(x, z):
            return tmap.get((float(x), float(z)))
    else:
        _t = float(target)
        target_kind = f"single datum {_t:.3f} m"

        def target_at(x, z):
            return _t
    moving = {}
    untargeted = 0
    for (x, z) in rect:
        t = target_at(x, z)
        if t is None:
            untargeted += 1
            continue
        y = ground.get(gkey(x, z))
        if y is not None and abs(y - t) > REGRADE_MOVE_M:
            moving[(float(x), float(z))] = (y, t)
    over = defaultdict(list)
    for s in solids:
        if not s.verts or s.name not in geom:
            continue
        if (s.name, tuple(round(v, 3) for v in s.pos)) in skip:
            continue
        for (x, z) in piece_columns(s):
            k = (float(x), float(z))
            if k in moving:
                over[k].append(s)
    buries, undermines, bearing, aloft, under = [], [], 0, 0, 0
    for k, ss in over.items():
        x, z = k
        now, tgt = moving[k]
        for s in ss:
            sp = columns.span(s, x, z)
            if sp is None:
                continue
            lo, hi = sp
            if lo - now > REGRADE_BEARING_M:
                aloft += 1                      # rule 2: not on this ground
                continue
            bearing += 1
            # RULE 6: BURIAL IS A TRANSITION.  A piece whose top is ALREADY
            # under the present ground cannot be buried by raising it further;
            # its burial is PRE-EXISTING, the way the rim gate separates a step
            # this write CAUSES from one it inherits.  MEASURED on Fresh42's
            # build: T6 was refused for "burying" a stone_stair and five walls
            # and floors spanning 34.683..35.683 m under ground at 60.03 m --
            # already 24.6 m down, the interior of a generated underground
            # location -- and T4 for a piece_groundtorch whose top (79.079) was
            # already 0.78 m under its ground (79.86).
            if (REGRADE_BURIAL_IS_A_TRANSITION and tgt > now
                    and hi < now - REGRADE_BEARING_M):
                under += 1
                continue
            if tgt > now and tgt > hi - REGRADE_BEARING_M:
                buries.append(dict(prefab=s.name, at=[x, z],
                                   span=[round(lo, 3), round(hi, 3)],
                                   ground=round(now, 3),
                                   target=round(tgt, 3),
                                   over_its_top_m=round(tgt - hi, 3)))
            elif tgt < now and lo - tgt > STEP_LIMIT_PER_M:
                undermines.append(dict(prefab=s.name, at=[x, z],
                                       span=[round(lo, 3), round(hi, 3)],
                                       ground=round(now, 3),
                                       target=round(tgt, 3),
                                       foot_in_air_m=round(lo - tgt, 3)))
    buries.sort(key=lambda r: -r["over_its_top_m"])
    undermines.sort(key=lambda r: -r["foot_in_air_m"])
    fills = sum(1 for y, t in moving.values() if t > y)
    return dict(
        verdict="REFUSE" if (buries or undermines) else "CLEAR",
        rect_samples=len(rect), moving=len(moving), fills=fills,
        cuts=len(moving) - fills,
        solids_over_a_moving_sample=sum(len(v) for v in over.values()),
        ground_bearing=bearing, aloft_and_not_in_the_question=aloft,
        already_under_the_ground_not_buried_by_this_write=under,
        buries=buries[:8], undermines=undermines[:8],
        excluded=len(skip),
        target_kind=target_kind,
        samples_without_a_target=untargeted,
        population=(f"every build-piece solid whose OWN ROTATED FOOTPRINT "
                    f"covers one of the {len(moving)} lattice samples this "
                    f"write actually MOVES, and whose own span bottom sits "
                    f"within {REGRADE_BEARING_M:g} m of the CURRENT applied "
                    f"surface there - {bearing} of "
                    f"{sum(len(v) for v in over.values())} such solids are "
                    f"ground-bearing, {aloft} are carried by something else "
                    f"and are not in the question, and {under} are ALREADY "
                    f"under the present ground, so a fill does not bury them"))


# ---------------------------------------------------------------------------
# THE CORPUS-SCOPE REFUSAL.  ONE GUARD, ON THE GATE SIDE, FOR EVERY SECTION
# THAT READS A REPO-SINGLETON CORPUS.
#
# THE DEFECT, MEASURED 2026-09-23 across all eleven sections.  Three sections
# open a file that lives at ONE PATH IN THE REPOSITORY and describes ONE
# WORLD, and not one of them asked which world:
#
#   accept/sections/roads.py::crossing_spans        roads/crossings.yaml
#   accept/sections/roads.py, declaration census    roads/crossings.yaml
#   accept/sections/annulus.py::run                 settlements/sites.yaml
#   accept/sections/townsites.py::run               settlements/sites.yaml
#
# Both of those files declare `world: Ulfsland / seed: Pirate68` in their own
# headers.  On `--world Fresh42` all four reads SUCCEEDED and the sections
# reported about Ulfsland.  `townsites` is the worst of the four: it audits
# the published site set against the declaration in force FOR THE SEED THAT
# FILE NAMES, so on a fresh world it printed PASS ABOUT A WORLD THE RUN WAS
# NOT ASKED ABOUT - a green verdict is worse than a red one here, because a
# red one gets looked at.
#
# IT IS THE DEFECT CLASS THIS TREE HAS NOW FOUND EIGHT TIMES: A KEY THAT
# NAMES A SUPERSET OR A SUBSET OF WHAT THE QUANTITY ACTUALLY DEPENDS ON.  The
# repository path is the key; the WORLD is what the quantity depends on.
#
# THE PLANNER SIDE ALREADY HAD THE ANSWER AND THE GATE SIDE DID NOT.
# `roads/spec.py` and `roads/structures.py::load_segment_doc` put every read
# of these same two files through `worldseed.require_scope`.  NOTHING BELOW
# IS A SECOND ONE: the comparison, and every word of the mismatch text, are
# still `require_scope`'s.  What is here is the ADAPTER that turns its raise
# into the `Finding` a gate section reports, plus the ONE clause it
# deliberately declines to make for its own population - see `corpus_scope`.
#
# UNREAD IS NOT CLEAR, AND IT IS THREE DIFFERENT STATES:
#
#   ABSENT       reported, `ok=False advisory=True`.  A fresh world has not
#                built the file yet, and "I never looked" may not be printed
#                as "I looked and found none".  Same disposal, and the same
#                reason, as `emission.py`'s absent-output row.
#   UNREADABLE   a HARD refusal.  `crossing_spans` used to log the exception
#                and return `[]`, so a corrupt corpus read as A WORLD WITH NO
#                CROSSINGS IN IT and every crossing check passed vacuously.
#                A section that cannot read its corpus REFUSES.
#   FOREIGN, OR  a HARD refusal naming the corpus, what it declares about
#   DECLARING    itself, and what this run was asked to judge.  A corpus with
#   NOTHING      no header is not a corpus that matches.
# ---------------------------------------------------------------------------

# WHAT THE GUARD'S ANSWER DEPENDS ON, DECLARED IN SOURCE, so a neuter that
# empties the guard shows up as a number instead of as a silent pass.  Each
# name is a field the guard MUST be able to change its verdict on, and the
# census case in `accept/sections/roads.py` varies each one in turn and
# REFUSES when any of them has stopped mattering.  THE DENOMINATOR IS THIS
# TUPLE, which no neuter patches: an index that answers over a population the
# neuter can shrink certifies itself.
CORPUS_SCOPE_DEPENDS_ON = ("corpus_seed", "corpus_world",
                           "run_seed", "run_world")


def _worldseed():
    """`tools/jumpstart/worldseed.py`.

    APPEND the jumpstart root, never insert it: `tools/jumpstart/network/` is
    a package that shadows `roads/network.py` for every later importer if it
    gets in front of `roads/`.  Off `HERE`, never off the module-global
    `JUMPSTART`, because a fixture may rebind that to a scratch tree.
    """
    root = str(HERE.parent)
    if root not in sys.path:
        sys.path.append(root)
    import worldseed                                       # noqa: PLC0415
    return worldseed


def run_scope(ctx) -> tuple[str, str]:
    """THIS RUN'S `(seed, world)`, off `ctx.args`, AND NEVER INSIDE A GUARD.

    Called BEFORE the `try` that wraps the read, for the reason
    `accept/sections/roads.py::owner_instruments` states at length: the
    shipped form of that read asked for `ctx.world`, which does not exist, and
    the `AttributeError` was caught by an `except Exception` written to
    survive an unparseable ledger.  The gate logged "the owner set is
    unreadable" and carried on, and `1A_FORBIDDEN` was 0 of 106 refusals
    gate-wide - not because nothing was forbidden, but because nothing was
    ever asked.  A ctx-wiring fault must CRASH the section; only the reads
    that depend on the world's own files may be guarded.
    """
    ws = _worldseed()
    args = ctx.args
    return (str(getattr(args, "seed", None) or ws.seed()),
            str(getattr(args, "world", None) or ws.world()))


def corpus_scope(label: str, path, doc, *, want_seed: str, want_world: str,
                 produced_by: str = "",
                 require_world: bool = True) -> str | None:
    """`None` when `doc` declares THIS run's scope; the refusal text if not.

    The seed comparison, the world comparison and their wording are
    `worldseed.require_scope`'s, caught here rather than restated.
    """
    ws = _worldseed()
    d = doc if isinstance(doc, dict) else {}
    try:
        ws.require_scope(label, path, found_seed=d.get("seed"),
                         found_world=d.get("world"), seed_=want_seed,
                         world_=want_world, produced_by=produced_by)
    except SystemExit as exc:
        return str(exc)
    # THE ONE CLAUSE `require_scope` DECLINES TO MAKE, AND WHY IT BELONGS
    # HERE RATHER THAN THERE.  It tolerates a missing `world` when the seed
    # matches, and it is right to: its own population is SEED-SCOPED
    # artefacts, and a world-generator dump is a function of the seed alone
    # with no world to declare.  MOST GATE CORPORA ARE NOT THAT.  A gate
    # corpus is read to judge ONE SAVE; two saves can be built from one
    # seed; and `settlements/sites.yaml` records where THIS world's pads
    # were dug, not what the generator would produce.  So an undeclared
    # world is refused here for exactly the reason an undeclared seed is
    # refused there.
    #
    # `require_world=False` IS A DECLARATION THE CALLER MAKES ABOUT ITS OWN
    # CORPUS, ONE CALL SITE AT A TIME, AND IT DEFAULTS CLOSED.  The only
    # artefacts entitled to it are the ones that are a FUNCTION OF THE SEED
    # ALONE and carry `world: null` on purpose - `roads/geography.yaml` and
    # `settlements/plan.json` are the two in this tree.  A default of False
    # would re-open the whole defect for every corpus that has not got the
    # field yet, which is the state this guard was written to end.
    if require_world and not d.get("world"):
        return (f"REFUSING: {label} {path} carries no world declaration, so "
                f"it cannot be shown to be about {want_world!r}. This corpus "
                f"lives at ONE PATH in the repository and describes ONE "
                f"world, so an absent declaration is how one world's numbers "
                f"walk into another's verdict -- MEASURED 2026-09-23: four "
                f"unguarded reads of two such files reported about Ulfsland "
                f"on a run asked about another world, and one of them "
                f"reported it as a PASS."
                + (f" Produced by: {produced_by}." if produced_by else ""))
    return None


def read_scoped_corpus(ctx, label: str, relpath=None, *, resolve=None,
                       produced_by: str = "", require_world: bool = True,
                       check: str = "corpus_is_about_this_world"):
    """One corpus, read ONLY if it is about this run's world.

    Returns `(doc, findings)`.  `doc` is None whenever `findings` is
    non-empty, so a caller cannot accidentally proceed on a corpus this
    refused: there is nothing to proceed with.

    TWO WAYS TO NAME THE FILE, AND THE RESOLVER IS THE IMPORTANT ONE.

    `relpath` is a path under the module-global `JUMPSTART`, for a corpus
    with NO per-seed slot at either end - `roads/crossings.yaml` is one:
    `roads/network.py` writes exactly that name for every seed, so a fresh
    seed has nothing in scope to fall back to and a refusal is the only
    correct answer.  Resolved off the module global so a fixture that
    rebinds `instruments.JUMPSTART` to a scratch tree is read there.

    `resolve` is a callable `(seed) -> Path` FOR A CORPUS THAT DOES HAVE A
    PER-SEED SLOT, and it must be THE READER'S OWN RESOLVER, never a second
    one.  `settlements/sites.yaml` is the case: `roads/spec.py::sites_path`
    already answers `sites.<seed>.yaml` for any non-default seed and honours
    `$VH_SITES` over both, and `accept/sections/provenance.py:174` already
    asks it.  A GUARD THAT REFUSED A WORLD WHICH HAS A CORRECT CORPUS OF ITS
    OWN WOULD BE A BLOCKER WEARING A GUARD'S CLOTHES - the same fault this
    gate's own townsites section was written to undo - so the guard resolves
    the name the reader resolves, and only then asks what that file declares.
    """
    from .registry import Finding                          # noqa: PLC0415
    # RESOLVED BEFORE THE GUARD.  See `run_scope`.
    want_seed, want_world = run_scope(ctx)
    path = (Path(resolve(want_seed)) if resolve is not None
            else Path(JUMPSTART) / relpath)
    scope = {"corpus": str(path), "run_seed": want_seed,
             "run_world": want_world}
    if not path.exists():
        return None, [Finding(
            check=check, ok=False, advisory=True,
            message=(f"{label} {path} is not present in this checkout, so "
                     f"this run read nothing from it and nothing below is "
                     f"judged against it -- absent is not a pass"),
            numbers={**scope, "present": False})]
    try:
        import yaml                                        # noqa: PLC0415
        doc = yaml.safe_load(path.read_text())
    except Exception as exc:                               # noqa: BLE001
        return None, [Finding(
            check=check, ok=False,
            message=(f"REFUSING: {label} {path} IS UNREADABLE "
                     f"({type(exc).__name__}: {exc}), so this section has no "
                     f"corpus. Until 2026-09-23 this read logged the "
                     f"exception and returned an empty list, and a corrupt "
                     f"corpus therefore scored identically to a world with "
                     f"nothing in it"),
            numbers={**scope, "present": True, "readable": False})]
    why = corpus_scope(label, path, doc, want_seed=want_seed,
                       want_world=want_world, produced_by=produced_by,
                       require_world=require_world)
    if why is None:
        return doc, []
    d = doc if isinstance(doc, dict) else {}
    return None, [Finding(
        check=check, ok=False,
        message=(f"{why} THE CORPUS {path} DECLARES world="
                 f"{d.get('world')!r} seed={d.get('seed')!r}; THIS RUN WAS "
                 f"ASKED ABOUT world={want_world!r} seed={want_seed!r}"),
        numbers={**scope, "present": True, "readable": True,
                 "declared_seed": d.get("seed"),
                 "declared_world": d.get("world")})]
