#!/usr/bin/env python3
"""The build ledger: the schema, and why each field is in it.

WHAT THIS IS.  The operator asked for "a log of everything you build and where
and how so that you can repeat it all during the next regeneration".  That is
not documentation.  It is a REPLAY ARTEFACT: a machine-readable, append-only,
hash-chained record of every world-mutating operation, in order, carrying
enough to reproduce the operation byte-for-byte AND enough to DETECT that the
world it is being replayed into is no longer the world it was recorded from.

The second half is the hard half, and it is what separates a faithful replay
from a plausible one.  The seed is the same, so the terrain is the same -- but:

  * The MOD SET decides what `ZoneSystem::PlaceLocations` puts in a zone.
    More_World_Locations contributes 190 POIs.  A replay on a different mod set
    can flatten a pad on top of a location that did not exist when the pad was
    solved, which is exactly how the old world lost POI content.
    -> `header.mods`, and `requires.mods` per op.
  * A TCData sample is a DELTA, applied to the GENERATED height
    (`TerrainComp::ApplyToHeightmap`).  If the generated height changes -- a new
    game build, a new worldgen version -- the same delta puts the ground
    somewhere else, silently, and the first symptom is a building in a hole.
    -> `terrain_write.entries[].generated_heights_sha256`, digested from the
       patchscan lattice, and refused per zone at replay.
  * A blueprint BODY is a file on a host outside this repo, four `old`s deep.
    A body that resolves to a different file is a different building.
    -> `spawn_plan.params.body.sha256`, and the emitted plan's own digest.
  * A PREFAB can vanish when a mod is removed.  `spawn_object` on an unknown
    prefab is a no-op with a cheerful echo.
    -> `requires.prefabs`, probed before the first command is sent.

WHAT THE LEDGER REFUSES TO DO.  There is no repair mode and no fixup mode.  On
drift the replay stops and reports.  Every silent substitution this project has
paid for -- a stale cache entry, a name collision resolving to the smaller of
two castles, a zero-byte body winning resolution -- would have been cheaper as a
refusal.

FOUR INVARIANTS ENFORCED AT WRITE TIME, so that a class of defect becomes
unreproducible by construction rather than by memory:

  1. `expect` IS MANDATORY ON EVERY MUTATING OP.  An operation that cannot state
     a postcondition is a fact worth failing on, not a field worth skipping.
     Tonight's pattern was operations that appeared to succeed.
  2. FLATTEN IS FORBIDDEN UNDER AN OVER-WATER STRUCTURE.  A structure op may
     declare `params.flatten = "FORBIDDEN"`; a `terrain_write` whose footprint
     overlaps one is REFUSED.  This is the `early-dock` defect: the pad levelled
     the rectangle, the water under the pier went away, and an over-water body
     stood on dry ground reading as the floating defect.
  3. ONE ZONE HOLDS ONE COMPILER.  `Heightmap::GetAndCreateTerrainCompiler`
     returns the FIRST compiler in a zone, and the write path issues
     `deleteObjects -zone ZX ZZ -prefab _TerrainCompiler -force` before
     spawning.  So a second `terrain_write` to a zone DESTROYS the first one's
     terrain.  A 20 km ribbon crosses ~300 zones and will meet pad zones, so
     this is the normal case, not a corner case.  A `terrain_write` to a zone an
     earlier `terrain_write` touched is REFUSED unless it names that earlier
     seq in `entries[].merged_from` and sets `merge_policy`.
  4. A PORTAL TAG HAS EXACTLY TWO ENDS.  `Game::FindRandomUnconnectedPortal`
     pairs on exact string equality and then draws `Random.Range(0, count)`.
     One end pairs with a random mod shrine -- the operator's one-way trip.
     Three ends make the pair nondeterministic on every load.  Uniqueness is
     therefore on (tag -> exactly 2 portal ZDOs), checked at `close()`, not on
     (tag -> 1 installation): `sandbox-harbour` legitimately duplicates
     `early-dock` at the same coordinates.

ENVELOPE.  One JSON object per line, UTF-8, no embedded newlines:

    {"seq": 1,
     "ts": "2026-09-16T02:00:00Z",
     "actor": "RoadNet",
     "op": "terrain_write",
     "params": {...},          # values AS SENT, not as intended
     "wire": ["deleteObjects -zone ...", "spawn_object _TerrainCompiler ..."],
     "requires": {"mods": [...], "prefabs": [...], "blobs": ["<sha256>"]},
     "expect": {...},          # a cheap postcondition, mandatory when mutating
     "meta": {...},            # free-form, unverified, for humans
     "prev": "<sha256 of the previous line's canonical bytes>"}

`wire` is the literal console command strings.  Replay's default mode re-sends
them verbatim; that is what makes the replay byte-faithful rather than a
re-derivation that happens to agree.  `params` exists so a human, and a future
re-derivation, can read what those strings mean.

Blobs -- emitted plan files, TCData byte arrays, patchscan lattices -- live in
`blobs/<sha256>` beside the ledger and are referenced by digest.  They are
content-addressed, so the same plan written twice costs one file.

WHAT THIS LEDGER CANNOT CAPTURE
===============================
Written down because this is where the next surprise lives, and because an
artefact that does not state its limits gets trusted past them.

 1. WORLD GENERATION ITSELF.  The ledger replays EDITS, not worldgen.  It can
    DETECT that the generated heights under a zone changed
    (`generated_heights_sha256`) and refuse; it cannot correct for it, and it
    cannot reproduce the terrain.  That comes from the seed and the game
    build, which is why both are pinned in the header.
 2. THE FRESH-REGENERATION HALF OF THE ROUND TRIP IS UNPROVEN HERE.  The
    round-trip proof ran build -> wipe -> replay on the SAME running world.
    That the same seed regenerates identical zone contents is INFERRED from
    `PlaceVegetation` seeding its RNG with
    `worldSeed + zoneX*4271 + zoneZ*9187 + prefabHash` (measured in IL by the
    clearing work), not measured by a regeneration.
 3. ZDO IDENTITY AND ANYTHING KEYED OFF IT.  ZDO ids are (server uid,
    counter), assigned at creation.  MEASURED across the proof: ids
    1115-1118 became 1119-1122 for byte-identical objects.  Portal PAIRING is
    likewise re-drawn on load by `Game::FindRandomUnconnectedPortal`, so the
    ledger can guarantee a tag has exactly two ends, never which connection
    hash they end up sharing.
 4. ANYTHING A PLAYER DID.  Loot taken, ground hoed, pieces damaged or
    repaired, wards, personal map pins, character state.  The ledger records
    what the pipeline SENT; it is not a diff of the world.
 5. TIME AND SPAWNER STATE.  `_ZoneCtrl` spawn state, raid/event state,
    creature populations, item despawn, wear-and-tear decay.  A replayed world
    is the built world at t=0, not the world as it was when it was measured.
 6. THE BODIES AND THE MODS THEMSELVES.  Blueprint bodies are deliberately not
    vendored and mod DLLs are not copied here.  The ledger carries digests and
    REFUSES on mismatch; it cannot restore a corpus that has been deleted.
 7. PER-PIECE PAYLOAD VERIFICATION AT SCALE.  A single spawn's ZDO strings can
    be read back (`findObjects -detailed` prints a sign's `Text:` and a
    portal's tag -- MEASURED).  A 1,907-piece body cannot: listing it is the
    question whose answer wedges the console sink.  So a `spawn_plan` is
    verified by COUNT and by the plan's digest, and its per-piece `data=`
    payloads are verified only transitively.
 8. OUT-OF-BAND WRITES.  The zone-clobber and flatten-FORBIDDEN invariants see
    only what goes through this ledger file.  A player, another tool, or an
    ad-hoc RCON command is invisible to them -- including the unrecorded
    `wipe` the round-trip proof itself used.
 9. WHETHER THE BUILD IS ANY GOOD.  This proves REPRODUCTION, not quality.  A
    road that is unwalkable replays perfectly.
10. THE REST OF THE DEPLOYMENT.  The modifier set is captured from a live
    `globalKeys` observation and the mod DLLs are hashed, but server config,
    `SERVER_ARGS`, the `admin_mode` overlay and `ServerCharacters`
    templates are not.  A replay into a correctly-seeded world with a
    different server config will pass preflight.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"

GENESIS_PREV = "0" * 64

# --------------------------------------------------------------------------
# The world this ledger is about.  Pinned in the header, refused on mismatch at
# replay: a replay pointed at the wrong world should stop at line one rather
# than at zone four hundred.  MEASURED by parsing
# config_merged/worlds_local/Ulfsland/_main.1.fwl2 -- a ZPackage of
# int32 version, string name, string seedName, int32 seed, int64 uid,
# int32 worldGenVersion.
ULFSLAND = {
    "world": "Ulfsland",
    "seed_name": "Pirate68",
    "seed": 147627509,
    "world_version": 41,
    "world_gen_version": 2,
}

# `hostops/anchor_game_build.sh WORLD --show` reads this; reusing it rather
# than inventing a second convention.
GAME_BUILD_ANCHOR_DEFAULT = (
    "1231fc2ffdbe6038ba622b8646c2084980d06962e5f8521ae5a0b886be0f1c61")


class LedgerError(RuntimeError):
    """A write or a replay that must not be allowed to proceed quietly."""


# --------------------------------------------------------------------------
# Operation registry
# --------------------------------------------------------------------------

class Op:
    """One operation kind.

    `mutating` decides whether `expect` is mandatory and whether replay will
    send anything.  `idempotent` is the honest answer to "what happens if this
    runs twice", and it is what the resume path consults:

      "yes"    re-running is harmless (the op deletes before it writes, or the
               server skips already-done work)
      "guard"  re-running DUPLICATES, so replay must measure first and skip
      "n/a"    nothing is sent
    """

    __slots__ = ("kind", "mutating", "idempotent", "required", "optional", "why")

    def __init__(self, kind: str, *, mutating: bool, idempotent: str,
                 required: tuple[str, ...], optional: tuple[str, ...], why: str):
        self.kind = kind
        self.mutating = mutating
        self.idempotent = idempotent
        self.required = required
        self.optional = optional
        self.why = why


_ROLE = ("role", "provisional", "site_id", "installation_id", "flatten",
         "flatten_reason", "note")

OPS: dict[str, Op] = {
    "world_manifest": Op(
        "world_manifest", mutating=False, idempotent="n/a",
        required=("schema_version", "world", "seed_name", "seed",
                  "world_version", "world_gen_version", "game_build_anchor",
                  "modifiers", "mods", "tool"),
        optional=("db_bytes", "fwl_sha256", "host", "started"),
        why="seq 0.  Pins the world and the environment the rest of the "
            "ledger was recorded against.  Replay refuses on mismatch."),

    "zones_generate": Op(
        "zones_generate", mutating=True, idempotent="yes",
        required=("pos", "max_m", "zones"),
        optional=_ROLE + ("timeout_s", "empty", "fault"),
        why="Upgrade World `zones_generate pos=X,Z max=M` + `start`.  Creates "
            "the vegetation and locations that do not otherwise exist on a "
            "dedicated server with no peers.  Idempotent: SpawnZone skips a "
            "zone that IsZoneGenerated.  WITH `params.empty` the wire carries "
            "Upgrade World's `empty` flag, which is a DIFFERENT operation "
            "wearing the same verb: `Generate.ExecuteZone` then does "
            "`m_generatedZones.Add(zone)` and returns, spawning NOTHING.  It "
            "deletes no ZDO, so it is not destructive, but it PERMANENTLY "
            "forfeits that zone's ungenerated content -- its vegetation and "
            "any location registered there will never be placed -- so "
            "`empty` requires `fault`, the MEASURED reason ordinary "
            "generation cannot be used, and its postcondition is "
            "`expect.zone_marked_generated` rather than `zone_ctrl`: an "
            "empty-marked zone has no `_ZoneCtrl` until `zones_restore` puts "
            "one there, and a check for one would fail for the right reason "
            "at the wrong time."),

    # A GENERATED ZONE WITH NO CONTROL OBJECT, and why this is its own op
    # rather than a relabelled `zones_generate`.  The two commands do not
    # even look at the same zones: `ZonesGenerate` sets
    # `TargetZones = Ungenerated` and `RestoreZones` sets
    # `TargetZones = Generated` (Upgrade World 1.82 sources, verified against
    # the deployed DLL's command table), so `Zones.GetZones` hands them
    # DISJOINT candidate sets and neither can ever do the other's work.
    # MEASURED on Ulfsland: `zones_restore pos=2368,-3328 max=1` answered
    # "0 zones: 448 skipped by the command" -- 448 being the whole generated
    # set, distance-filtered to nothing -- which is also the cleanest live
    # proof that zone (37,-52) was NOT in that set.
    #
    # So the fault this repairs is precise: a zone that IS generated and
    # holds no `_ZoneCtrl`.  `RestoreZones.ExecuteZone` reads the zone's ZDOs,
    # and if none carries `m_zoneCtrlPrefab`'s hash it calls
    # `ZDOMan.CreateNewZDO(zonePos, hash)` -- one object, at the zone
    # position, nothing removed.  That is the state an `empty`-marked zone is
    # in by construction (see `zones_generate params.empty`), and the state
    # `locations_add` used to leave behind, which is the bug the command was
    # written for.
    #
    # NON-DESTRUCTIVE, stated rather than implied: it ADDS a missing control
    # object and removes nothing, so it does not spend the destructive budget
    # and needs no clearing census.  Its sibling `zones_reset` IS destructive
    # -- it makes zones ungenerated and defaults to 412 of them on this world
    # -- and is deliberately NOT modelled here.
    "zones_restore": Op(
        "zones_restore", mutating=True, idempotent="yes",
        required=("pos", "max_m", "zones", "fault"),
        optional=_ROLE + ("timeout_s",),
        why="Upgrade World `zones_restore pos=X,Z max=M` + `start`, staged in "
            "a `stop`/cmd/`start` bracket like every other Upgrade World "
            "operation.  Repairs a zone that IS in the save's generated set "
            "and holds no `_ZoneCtrl` -- which `zones_generate` can never do, "
            "because it only ever looks at UNGENERATED zones.  "
            "Non-destructive: it adds the missing "
            "control object, deletes nothing, and spends no destructive "
            "budget.  Idempotent: a zone that already has its control object "
            "gains nothing.  Postcondition is its own and is the whole point "
            "-- `expect.zone_ctrl_exact`, EXACTLY ONE `_ZoneCtrl` per "
            "declared zone, probed by a disc wholly inside that zone's 64 m "
            "square so no neighbour can answer on its behalf.  `fault` is "
            "the measurement that says why the zone needed repairing, "
            "because an op that mutates a zone on a hunch is a guess."),

    "objects_clear": Op(
        "objects_clear", mutating=True, idempotent="yes",
        required=("centre", "radius_m", "ids", "ignore"),
        optional=_ROLE,
        why="Upgrade World `objects_remove id=* ignore=_* pos=X,Z max=R`.  A "
            "vertical cylinder on Utils.DistanceXZ, measured per OBJECT.  "
            "`id=*` is REQUIRED or it silently removes nothing."),

    "terrain_write": Op(
        "terrain_write", mutating=True, idempotent="yes",
        # target_y XOR profile is checked below, not here: a pad has a single
        # target height, a road has a longitudinal profile, and neither shape
        # can express the other.
        required=("name", "paint", "datum", "entries", "location_check"),
        optional=_ROLE + ("target_y", "profile", "apron_m", "pad_w", "pad_d",
                          "max_cut_m", "max_fill_m", "over_clamp"),
        why="The `_TerrainCompiler` write: a `deleteObjects -zone` then a "
            "`spawn_object _TerrainCompiler from=cx,cz,0 data=<entry>` per "
            "zone.  Idempotent BECAUSE of the delete -- which is also why it "
            "clobbers an earlier write to the same zone (see merged_from)."),

    "spawn": Op(
        "spawn", mutating=True, idempotent="guard",
        required=("prefab", "pos", "yaw_deg"),
        optional=_ROLE + ("rot_sent", "from", "data_b64", "data_sha256",
                          "zdo_strings", "scale", "datum", "guard_radius_m"),
        why="One World Edit Commands `spawn_object`.  `pos=` is z,x,y and "
            "`rot=` is euler y,x,z and `from=` is x,z,y -- three component "
            "orders in one command, all MEASURED from IL.  `params.pos` is "
            "WORLD order (x, y, z); `wire` holds the sent order verbatim."),

    "spawn_plan": Op(
        "spawn_plan", mutating=True, idempotent="guard",
        required=("plan_sha256", "anchor", "command_count", "prefabs"),
        optional=_ROLE + ("body", "datum", "pad_height", "batch_bytes",
                          "plan_ref", "align", "reach_m"),
        why="A batch of `spawn_object` lines from one blueprint body.  The "
            "plan file is a blob; `body.sha256` is the blueprint's own digest, "
            "which is what detects a body resolving to a different file."),

    "portal": Op(
        "portal", mutating=True, idempotent="guard",
        required=("prefab", "pos", "yaw_deg", "tag", "pair_tag"),
        optional=_ROLE + ("rot_sent", "from", "data_b64", "data_sha256",
                          "zdo_strings", "datum", "guard_radius_m"),
        why="A `spawn` whose ZDO string `tag` (hash 696029674 = "
            "StableHashCode(\"tag\") = ZDOVars.s_tag) makes it pair.  Its own "
            "op kind only because the two-ends-per-tag invariant needs one "
            "place to look."),

    "stock": Op(
        "stock", mutating=True, idempotent="guard",
        required=("target", "items"),
        optional=_ROLE,
        why="Container inventory written into an existing ZDO."),

    "retire": Op(
        "retire", mutating=True, idempotent="yes",
        required=("prefab", "pos", "radius_m", "retires"),
        optional=_ROLE + ("reason",),
        why="`deleteObjects -prefab P -near x y z r -force`.  A demolition, "
            "recorded so that a REPLAY ENDS WITH THE OBJECTS GONE rather than "
            "faithfully rebuilding litter.  `retires` names the seq numbers "
            "this removes, which is what lets the portal-pair invariant stop "
            "counting a deleted end -- otherwise the ledger asserts something "
            "true about the log and false about the world.  It also gives a "
            "mis-placed body's demolition somewhere to live: tonight a "
            "1,907-piece building was demolished and re-placed and that "
            "history had nowhere to go.  The reply is one line PER DELETED "
            "OBJECT (155 bytes MEASURED), so a retire must be scoped small "
            "enough that its ANSWER is short."),

    "save": Op(
        "save", mutating=True, idempotent="yes",
        required=(),
        optional=_ROLE,
        why="`save`.  Recorded because WHERE a save fell decides what a "
            "crash-interrupted replay resumes from."),

    "observe": Op(
        "observe", mutating=False, idempotent="n/a",
        required=("what", "method", "tool", "value"),
        optional=_ROLE + ("units", "at"),
        why="A measurement, with its PROVENANCE.  `method` must start "
            "MEASURED or INFERRED; `tool` names the script or command that "
            "produced it.  Both are required because a number whose origin is "
            "a transcript cannot be told apart from a number that came out of "
            "a shared kernel where another agent had rebound the global it "
            "was computed from -- MEASURED tonight, twice, on globals named "
            "`f`, `B` and `F`.  A computation that confidently answers a "
            "question using somebody else's data announces nothing, so the "
            "log has to carry the origin."),

    "chain_reset": Op(
        "chain_reset", mutating=False, idempotent="n/a",
        required=("broken_at_line", "expected_prev", "actual_prev"),
        optional=_ROLE + ("duplicate_seqs", "branches", "seq_advisory_from_line",
                          "cause", "text"),
        why="DOCUMENTS a chain fork instead of editing it away.  Two "
            "concurrent appends can write two branches carrying the same "
            "`prev` (MEASURED: file line 47 of Ulfsland's ledger).  The file "
            "is never renumbered and no `prev` is ever rewritten -- a log "
            "edited to look clean is worth less than one with a labelled "
            "wart -- so the fork is named here, by FILE LINE, and the two "
            "digests are carried so a reader can verify the claim against "
            "the file rather than trust it.  `replay.py` refuses an "
            "UNDOCUMENTED fork and verifies both digests, so a reset cannot "
            "launder a fork it does not accurately describe.  Param names are "
            "the ones BuildLedger's reader reads: broken_at_line, "
            "expected_prev, actual_prev."),

    "note": Op(
        "note", mutating=False, idempotent="n/a",
        required=("text",),
        optional=_ROLE,
        why="A boundary agreement or a rationale.  'Fords are RoadNet terrain, "
            "not Crossings structures' will be re-litigated in three months by "
            "whoever reads this, and it belongs in the log rather than in a "
            "hub transcript nobody keeps."),
}

MUTATING = {k for k, v in OPS.items() if v.mutating}

# Roles are a taxonomy, not new op kinds: one executor, the agent's own
# vocabulary preserved in the log the operator reads.
KNOWN_ROLES = {
    # RoadNet
    "road_segment", "ford", "road_junction", "road_shoulder",
    # Crossings
    "bridge_assembly", "bridge_body_place", "dock_place", "harbour_place",
    "boathouse_place", "ferry_terminal_place", "boat_spawn_moor",
    # Settlements
    "town_body", "town_plot", "watchtower", "castle", "lighthouse", "treehouse",
    # WayFinding
    "waypoint_sign", "directory_sign", "map_pin", "spawn_portal",
    # GroundTruth / shared
    "site_pad", "site_body", "site_prop", "clearing",
}

TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,10}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


# --------------------------------------------------------------------------
# Canonicalisation and the hash chain
# --------------------------------------------------------------------------

def canonical(record: dict) -> bytes:
    """The bytes a record's digest is taken over.

    Sorted keys and no insignificant whitespace, so the digest depends on the
    CONTENT and not on the order a caller happened to build the dict in.  A
    record that round-trips through JSON must digest the same, or the chain is
    not a chain.
    """
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(record: dict) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def scan(raw_lines: Iterable[str]) -> dict:
    """Walk the ledger in FILE ORDER and report its integrity AS DATA.

    THIS DOES NOT RAISE ON A FORKED LINKAGE, and that is a deliberate
    correction. MEASURED tonight: `Ledger._append_raw` read the chain head,
    computed `prev` and wrote, with no lock and with the head CACHED at open,
    so two concurrent appenders wrote two branches carrying the same `prev`
    (file line 47, seqs 43-46 duplicated). The old `chain_head` raised, which
    meant `Ledger.__init__` raised, which meant EVERY agent lost the ability
    to append -- including the ability to append a record SAYING the log was
    damaged. An integrity check whose only failure mode is total refusal
    cannot record its own finding.

    So: opening to APPEND tolerates a labelled fork; opening to REPLAY must
    refuse one, because a replay from an ambiguous order is not a replay of
    what was built. Two different questions, and the same function was
    answering both with the stricter answer.

    A `prev` that matches NO line's digest is still fatal here: that is an
    edit, a truncation or a reorder rather than a race, and nothing about it
    is recoverable by labelling.

    `seq` is ADVISORY from the first fork onward -- it is no longer unique --
    so the resume order and the head are taken from FILE POSITION and from the
    LAST LINE, never from a maximum or a count.
    """
    seen: dict[str, int] = {GENESIS_PREV: -1}
    seq, head = -1, GENESIS_PREV
    forks: list[dict] = []
    seq_lines: dict[int, list[int]] = {}
    count = 0
    # A fork counts as DOCUMENTED only when a `chain_reset` record names it by
    # FILE LINE and states BOTH digests correctly. A prose `note` mentioning
    # the word "fork" is not evidence -- that would let a reset launder a fork
    # it does not accurately describe, which is the same shape as a check
    # answering a question it is not measuring. The param names are the ones
    # `replay.py` reads: broken_at_line, expected_prev, actual_prev.
    resets: list[dict] = []
    for index, raw in enumerate(raw_lines):
        raw = raw.strip()
        if not raw:
            continue
        rec = json.loads(raw)
        prev = rec.get("prev")
        if prev != head:
            if prev not in seen:
                raise LedgerError(
                    f"chain break at file line {index} (seq {rec.get('seq')}): "
                    f"prev={prev} matches NO line in this ledger.  That is an "
                    f"edit, a truncation or a reorder rather than a concurrent "
                    f"append, and a replay from it would not be a replay of "
                    f"what was built.")
            forks.append({"line": index, "seq": rec.get("seq"),
                          "actor": rec.get("actor"), "op": rec.get("op"),
                          "ts": rec.get("ts"), "declared_prev": prev,
                          "actual_prev": head})
        if rec.get("op") == "chain_reset":
            resets.append(rec.get("params") or {})
        seq = rec.get("seq", seq)
        seq_lines.setdefault(seq, []).append(index)
        head = digest(rec)
        seen[head] = index
        count = index + 1
    for f in forks:
        f["documented"] = any(
            r.get("broken_at_line") == f["line"]
            and r.get("expected_prev") == f["declared_prev"]
            and r.get("actual_prev") == f["actual_prev"] for r in resets)
    return {"count": count, "last_seq": seq, "head": head, "forks": forks,
            "duplicate_seqs": {s: ls for s, ls in seq_lines.items()
                               if len(ls) > 1},
            "undocumented_forks": [f for f in forks if not f["documented"]]}


def chain_head(path_lines: Iterable[str]) -> tuple[int, str]:
    """(last seq, digest of the last line) for an existing ledger file.

    The seq comes from the LAST LINE rather than from a count, because after a
    fork the numbers are duplicated and a count would hand the next appender a
    seq that already exists twice.
    """
    s = scan(path_lines)
    return s["last_seq"], s["head"]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _xz(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 2 and all(
        isinstance(c, (int, float)) for c in v)


def _xyz(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 3 and all(
        isinstance(c, (int, float)) for c in v)


def validate(record: dict) -> list[str]:
    """Every problem with one record, as a list.  Empty means acceptable.

    Deliberately returns ALL problems rather than raising on the first: an
    agent fixing its emitter wants the whole list, and a half-fixed emitter
    that fails again on the next field is how a build loses an hour.
    """
    bad: list[str] = []
    op = record.get("op")
    spec = OPS.get(op)
    if spec is None:
        return [f"unknown op {op!r}; known: {sorted(OPS)}"]

    for key in ("seq", "ts", "actor", "op", "params", "wire", "requires",
                "meta", "prev"):
        if key not in record:
            bad.append(f"missing envelope key {key!r}")
    if bad:
        return bad

    if not isinstance(record["actor"], str) or not record["actor"]:
        bad.append("actor must be a non-empty agent id")
    if not isinstance(record["params"], dict):
        bad.append("params must be an object")
        return bad
    if not isinstance(record["wire"], list) or not all(
            isinstance(w, str) for w in record["wire"]):
        bad.append("wire must be a list of literal command strings")
    if not isinstance(record["requires"], dict):
        bad.append("requires must be an object")

    p = record["params"]
    for key in spec.required:
        if key not in p:
            bad.append(f"{op}: params.{key} is required -- {spec.why}")

    known = set(spec.required) | set(spec.optional)
    for key in p:
        if key not in known:
            bad.append(f"{op}: params.{key!r} is not in the schema "
                       f"(known: {sorted(known)}).  Put unmodelled data in "
                       f"`meta`, which is free-form and unverified.")

    role = p.get("role")
    if role is not None and role not in KNOWN_ROLES:
        bad.append(f"role {role!r} is not registered; add it to KNOWN_ROLES "
                   f"rather than inventing it at the callsite, so the operator "
                   f"reading the log sees one vocabulary")

    # expect: mandatory when the op touches the world.  An operation that
    # cannot state a postcondition is a fact worth failing on.
    if spec.mutating:
        exp = record.get("expect")
        if not isinstance(exp, dict) or not exp:
            bad.append(
                f"{op}: `expect` is MANDATORY and non-empty on a mutating op. "
                f"State a cheap postcondition the replay can measure "
                f"(objects_count total, zone_ctrl count, tag read-back). "
                f"If you genuinely cannot, that is a fact to fail on.")
        if not record["wire"]:
            bad.append(f"{op}: a mutating op with no `wire` cannot be "
                       f"replayed.  Record the literal commands as sent.")
        # A TRAILING LITERAL `start` IS REFUSED ON AN OPERATION THAT FINISHES
        # IN ONE FRAME.  `replay.send_wire` already brackets a staged verb
        # with `stop`/cmd/`start`, so the extra line is a SECOND `start` --
        # harmless while a generate is still running across frames, which is
        # why the existing `zones_generate` records carry it, and fatal for
        # an instant one.  MEASURED tonight on the first `zones_restore`
        # emit: the command ran, the second `start` found no coroutine to
        # resume and ValheimRcon answered `routine is null`, so
        # `console_echo` correctly refused the reply and `emit` aborted
        # BEFORE measuring a postcondition that had in fact already been
        # decided.
        if op == "zones_restore" or (op == "zones_generate"
                                     and p.get("empty")):
            if any(str(w).strip() == "start" for w in record["wire"]):
                bad.append(
                    f"{op}: drop the literal `start` line from `wire`. "
                    f"`send_wire` brackets this staged verb with "
                    f"stop/cmd/start itself, and this operation completes in "
                    f"one frame, so the extra `start` finds a null coroutine "
                    f"and the reply is `routine is null` -- an op that "
                    f"WORKED, reported as unconfirmed.")
        if op == "zones_restore":
            bad += _zones_restore_expect(p, exp if isinstance(exp, dict)
                                         else {})
        if op == "zones_generate" and p.get("empty"):
            e = (exp if isinstance(exp, dict) else {})
            if "zone_ctrl" in e:
                bad.append(
                    "zones_generate params.empty with `expect.zone_ctrl`: an "
                    "empty mark spawns NOTHING -- `Generate.ExecuteZone` "
                    "returns straight after `m_generatedZones.Add` -- so a "
                    "_ZoneCtrl count is a postcondition this command cannot "
                    "satisfy. It is `zones_restore` that puts the control "
                    "object there, and that op carries the count.")
            if not isinstance(e.get("zone_marked_generated"), dict):
                bad.append(
                    "zones_generate params.empty requires "
                    "`expect.zone_marked_generated` -- the ONLY thing this "
                    "command changes is the save's generated-zone set, so "
                    "that set is what has to be measured. The probe re-sends "
                    "`zones_generate pos=<cx>,<cz> max=1` WITHOUT `start` "
                    "and requires the init line to report 0 zones: that "
                    "command targets UNGENERATED zones, so 0 selected is "
                    "exactly 'this zone is now in the generated set', and it "
                    "is the inverse of the '1 zones' that proved it was not.")

    bad += _validate_params(op, p)
    return bad


# The widest probe disc that is still WHOLLY INSIDE a 64 m zone square
# centred on the zone centre.  Half-extent is 32 m, so 32 touches the edge
# and anything beyond it counts a neighbour's control object -- which is how
# a check comes to confidently answer a question it is not measuring.
ZONE_INSIDE_MAX_R_M = 31.9


def _zones_restore_expect(p: dict, exp: dict) -> list[str]:
    """`zones_restore`'s own postcondition, refused at WRITE time.

    The op exists to put exactly one `_ZoneCtrl` back into a generated zone
    that has none, so the only postcondition worth recording is that count,
    measured where nothing else can supply it.  `>= 1` is not good enough
    here: two control objects in one zone is a different fault, and the
    diagnosis this op repairs was produced by discs drawn strictly inside the
    zone square precisely because a 64 m-wide answer is a neighbour's answer.
    """
    bad: list[str] = []
    c = exp.get("zone_ctrl_exact")
    if not isinstance(c, dict):
        return ["zones_restore: `expect.zone_ctrl_exact` is REQUIRED -- "
                "{count: 1, probe_radius_m: R}. A `zone_ctrl` count that "
                "passes on >= 1 is the postcondition of `zones_generate`, "
                "and it cannot tell this op's repair from the neighbouring "
                "zones that were already fine."]
    if c.get("count") != 1:
        bad.append(f"zones_restore: expect.zone_ctrl_exact.count is "
                   f"{c.get('count')!r}, and the only measurable meaning of "
                   f"'the control object is back' is EXACTLY 1. "
                   f"PlaceZoneCtrl places one per zone at the zone centre.")
    r = c.get("probe_radius_m")
    if not isinstance(r, (int, float)) or not 0 < float(r) <= ZONE_INSIDE_MAX_R_M:
        bad.append(f"zones_restore: expect.zone_ctrl_exact.probe_radius_m is "
                   f"{r!r}; it must be > 0 and <= {ZONE_INSIDE_MAX_R_M} so "
                   f"the disc is WHOLLY INSIDE the zone's 64 m square and no "
                   f"neighbour's `_ZoneCtrl` can answer for the zone under "
                   f"repair.")
    zones = p.get("zones")
    if isinstance(zones, list) and zones and "zone_ctrl" in exp:
        bad.append("zones_restore: carrying `expect.zone_ctrl` beside "
                   "`zone_ctrl_exact` records two postconditions for one "
                   "fact, and the weaker one (>= 1) is the one that would "
                   "pass on a zone this op did not repair. Drop it.")
    return bad


def _op_record_problems(where: str, rec: Any) -> list[str]:
    """The four gzip'd fields that separate a safe TCData blob from the one
    that produced the operator's floating grass.

    A blob digest cannot tell them apart -- the difference is inside the
    compressed payload -- so it is carried in the clear and checked here.

    MEASURED: grass is `ClutterSystem` clutter with NO ZDO, client-only, its Y
    baked from a downward raycast at patch-generation time and never
    re-evaluated.  `TerrainComp::CheckLoad` invalidates it with
    `if (m_operations == before + 1) ResetGrass(m_lastOpPoint, m_lastOpRadius)
    else ResetGrass(hmap.position, 32)`, and `before` is 0 on a fresh
    component.  `tcdata.py` wrote `operations = 1` with a ZEROED op point and
    radius, so every first load reset a zero-sized box at the WORLD ORIGIN,
    the pad's grass was never invalidated, and it recurred on every reload --
    which is exactly "still floating after I teleported back".
    """
    if not isinstance(rec, dict):
        return [f"{where}.op_record must be "
                f"{{operations, last_op_point, last_op_radius}} -- the blob "
                f"digest cannot distinguish a safe blob from the floating-"
                f"grass one, because the difference is four fields inside the "
                f"gzip"]
    bad = []
    ops = rec.get("operations")
    if not isinstance(ops, int):
        bad.append(f"{where}.op_record.operations must be an int")
    elif ops == 1:
        bad.append(
            f"{where}.op_record.operations == 1 is REFUSED. `before` is 0 on "
            f"a fresh TerrainComp, so operations==1 selects "
            f"CheckLoad's narrow `ResetGrass(m_lastOpPoint, m_lastOpRadius)` "
            f"branch -- and with a zeroed op record that resets a zero-sized "
            f"box at the world origin, leaving the pad's grass floating on "
            f"EVERY load. Write operations=2 so a first load cannot equal "
            f"before+1, with the op point at the zone centre and a real "
            f"radius.")
    pt = rec.get("last_op_point")
    if not _xyz(pt):
        bad.append(f"{where}.op_record.last_op_point must be [x, y, z] -- the "
                   f"zone centre at pad height")
    radius = rec.get("last_op_radius")
    if not isinstance(radius, (int, float)) or radius <= 0:
        bad.append(f"{where}.op_record.last_op_radius must be > 0; a zero "
                   f"radius resets nothing and is half of the defect")
    return bad


def _validate_params(op: str, p: dict) -> list[str]:
    bad: list[str] = []

    if op == "zones_generate":
        if not _xz(p.get("pos")):
            bad.append("zones_generate: pos must be [x, z]")
        if not isinstance(p.get("zones"), list) or not p["zones"]:
            bad.append("zones_generate: zones must be a non-empty list of "
                       "[zx, zz] -- the set whose CENTRE is within max_m of "
                       "pos, which is what makes the _ZoneCtrl count in "
                       "`expect` a real completion signal rather than a guess")
        if "empty" in p:
            if p["empty"] is not True:
                bad.append("zones_generate: params.empty is a FLAG on the "
                           "wire -- record it as True or leave it out, never "
                           "as False, or the params and the wire disagree "
                           "about which of two different operations ran")
            if not str(p.get("fault", "")).strip():
                bad.append(
                    "zones_generate params.empty requires `fault`: the "
                    "MEASURED reason ordinary generation cannot be used "
                    "here. An empty mark forfeits that zone's vegetation and "
                    "any location registered in it FOREVER -- it deletes no "
                    "ZDO, but the zone is never generated again -- so the "
                    "log carries why, not just that.")

    elif op == "zones_restore":
        if not _xz(p.get("pos")):
            bad.append("zones_restore: pos must be [x, z]")
        zones = p.get("zones")
        if not isinstance(zones, list) or not zones:
            bad.append("zones_restore: zones must be a non-empty list of "
                       "[zx, zz] -- the zones this repairs, one postcondition "
                       "probe each")
        try:
            max_m = float(p.get("max_m"))
        except (TypeError, ValueError):
            max_m = None
            bad.append("zones_restore: max_m must be the number sent as "
                       "`max=`, in metres from pos")
        # THE DECLARED SET MUST BE THE SET THE COMMAND SELECTS.  Upgrade
        # World picks zones by the distance from `pos` to the ZONE CENTRE
        # (MEASURED: `pos=-318,-64 max=82` answered "5 zones generated" and
        # exactly five zone centres lie within 82 m).  A declared zone the
        # command cannot reach would give the postcondition a zone the
        # operation never touched to pass or fail on, which is the same
        # defect as a check measuring the wrong question -- and this op's
        # whole value is that its postcondition is exact.
        if max_m is not None and _xz(p.get("pos")) and isinstance(zones, list):
            px, pz = float(p["pos"][0]), float(p["pos"][1])
            for z in zones:
                if not (_xz(z) and all(isinstance(c, int) for c in z)):
                    bad.append(f"zones_restore: zone {z!r} must be [zx, zz] "
                               f"integers")
                    continue
                d = ((z[0] * 64.0 - px) ** 2 + (z[1] * 64.0 - pz) ** 2) ** 0.5
                if d > max_m:
                    bad.append(
                        f"zones_restore: declared zone {z} has its centre "
                        f"{d:.2f} m from pos ({px:g}, {pz:g}) but max_m is "
                        f"{max_m:g}, so `zones_restore` will not select it "
                        f"and its postcondition would measure a zone this "
                        f"command never touched")
        if not str(p.get("fault", "")).strip():
            bad.append("zones_restore: `fault` must state the MEASURED reason "
                       "this zone needs its control object restored -- the "
                       "census that found zero `_ZoneCtrl` and the "
                       "`zones_generate` that was skipped. This op mutates a "
                       "zone; a mutation with no measured fault behind it is "
                       "a guess with a record.")

    elif op == "objects_clear":
        if not _xz(p.get("centre")):
            bad.append("objects_clear: centre must be [x, z]")
        if p.get("ids") != "*" and not isinstance(p.get("ids"), list):
            bad.append("objects_clear: ids must be \"*\" or a list. "
                       "`objects_remove` with only `ignore=` answers "
                       "'Error: Missing ids.' and removes nothing, silently.")


    elif op == "terrain_write":
        has_target = "target_y" in p
        has_profile = "profile" in p
        if has_target == has_profile:
            bad.append(
                "terrain_write: exactly one of params.target_y (a pad has one "
                "height) or params.profile (a road has a longitudinal "
                "profile) is required.  profile = {nodes: [[x,z,y],...], "
                "half_width_m, shoulder_m, interp}, and it is what lets a "
                "replay RE-DERIVE deltas after a worldgen change instead of "
                "only detecting the change and stopping.")
        # BLOCKING, and it is structural here because it is not wired
        # anywhere else.  MEASURED tonight: `flatten.py` has NO location
        # check and cut 6.65 m of ground out from under a `LocationProxy`
        # holding a `TreasureChest_meadows_buried` without complaint -- it
        # relies on `clearing/area.py`'s stand-off, which only runs when
        # CLEARING runs.  A ribbon crosses hundreds of zones where clearing
        # may not have run, and POI damage is the one defect the old world
        # could not repair.  So a terrain write states its check or it is not
        # written.
        lc = p.get("location_check")
        if not isinstance(lc, dict):
            bad.append(
                "terrain_write: params.location_check is REQUIRED -- "
                "{dump, method, nearest: {name, dist_m, clearance_m}, "
                "standoff_m, verdict}. `flatten.py` performs no location "
                "check of its own; nothing else will catch a pad cutting the "
                "ground out from under a LocationProxy.")
        else:
            for key in ("dump", "method", "nearest", "standoff_m", "verdict"):
                if key not in lc:
                    bad.append(f"terrain_write.location_check.{key} is "
                               f"required")
            if lc.get("verdict") != "clear":
                bad.append(
                    f"terrain_write.location_check.verdict is "
                    f"{lc.get('verdict')!r}, not 'clear'. A write that knows "
                    f"it is inside a location's stand-off is refused; move "
                    f"the route or the pad.")
            if not str(lc.get("method", "")).startswith(
                    ("MEASURED", "INFERRED")):
                bad.append("terrain_write.location_check.method must start "
                           "MEASURED or INFERRED and name how the stand-off "
                           "was derived -- per-type exteriorRadius/"
                           "interiorRadius, not one global number, which "
                           "MEASURED clears zero of 12,301 instances.")
        entries = p.get("entries")
        if not isinstance(entries, list) or not entries:
            bad.append("terrain_write: entries must be a non-empty list, one "
                       "per zone")
            return bad
        for i, e in enumerate(entries):
            where = f"terrain_write.entries[{i}]"
            if not isinstance(e, dict):
                bad.append(f"{where} must be an object")
                continue
            for key in ("zone", "centre", "data_entry", "blob_sha256",
                        "generated_heights_sha256", "zone_generated_before",
                        "zone_generated_probe", "op_record"):
                if key not in e:
                    bad.append(f"{where}.{key} is required")
            if "zone" in e and not (_xz(e["zone"]) and all(
                    isinstance(c, int) for c in e["zone"])):
                bad.append(f"{where}.zone must be [zx, zz] integers")
            for key in ("blob_sha256", "generated_heights_sha256"):
                if key in e and not SHA_RE.match(str(e[key])):
                    bad.append(f"{where}.{key} must be a lowercase sha256 hex "
                               f"digest")
            # THE ORDERING IS PROVEN AND STEP 0 IS NOT OPTIONAL.  MEASURED:
            # paint `paved_cleared` over all 1,681 samples of a pad in an
            # UNGENERATED zone and Bush01 x15, Beech1 x15, RaspberryBush x2
            # and eleven more prefabs were planted anyway, up to 7.68 m above
            # the pad, as real permanent ZDOs.  Cause: `Heightmap::Generate`
            # ends in `ApplyModifiers`, which finds its compiler by scanning
            # the static `TerrainComp::s_instances` list of INSTANTIATED
            # components -- and a server with no peers instantiates none, so
            # neither the deltas nor the cleared alpha ever reach the
            # heightmap before `SpawnZone` runs `PlaceVegetation`.  So a
            # terrain write into an ungenerated zone is not a risk, it is the
            # defect, and it is refused here.
            if e.get("zone_generated_before") is not True:
                bad.append(
                    f"{where}.zone_generated_before is "
                    f"{e.get('zone_generated_before')!r}: a terrain write "
                    f"into a zone that was never generated plants that zone's "
                    f"whole vegetation set on the UNMODIFIED generated height "
                    f"and ignores the paint's cleared alpha. `zones_generate` "
                    f"the zone first. Step 0 of the ordering is a hard "
                    f"precondition, not a convenience.")
            probe = str(e.get("zone_generated_probe", ""))
            if "_ZoneCtrl" not in probe:
                bad.append(
                    f"{where}.zone_generated_probe must be the exact command "
                    f"that OBSERVED the zone was generated, and it must name "
                    f"_ZoneCtrl. MEASURED: PlaceZoneCtrl puts exactly one "
                    f"_ZoneCtrl at the zone CENTRE, and objects_count's "
                    f"filter is an XZ cylinder, so "
                    f"`objects_count id=_ZoneCtrl pos=<cx>,<cz> max=1` is an "
                    f"exact two-line test. A boolean with no probe behind it "
                    f"is an assumption about what the pipeline did first.")
            bad += _op_record_problems(where, e.get("op_record"))
            if "merged_from" in e:
                if not isinstance(e["merged_from"], list) or not all(
                        isinstance(s, int) for s in e["merged_from"]):
                    bad.append(f"{where}.merged_from must be a list of earlier "
                               f"seq numbers")
                if e.get("merge_policy") not in ("union", "replace"):
                    bad.append(f"{where}.merge_policy must be 'union' (samples "
                               f"merged per index, last writer wins per "
                               f"sample) or 'replace' (deliberate total "
                               f"overwrite of the earlier write)")

    elif op in ("spawn", "portal"):
        if not _xyz(p.get("pos")):
            bad.append(f"{op}: pos must be [x, y, z] in WORLD order -- not the "
                       f"z,x,y the wire takes")
        if "data_b64" in p and "data_sha256" in p:
            bad.append(f"{op}: give data_b64 (inline) or data_sha256 (blob), "
                       f"not both")
        if "zdo_strings" in p and not isinstance(p["zdo_strings"], dict):
            bad.append(f"{op}: zdo_strings must be the DECODED string map that "
                       f"data_b64 encodes, e.g. {{'text': 'Iron-Era "
                       f"Workshop'}} -- a base64 payload is invisible to a "
                       f"diff, and this is what makes `expect` writable")
        if op == "portal":
            tag = p.get("tag")
            if not isinstance(tag, str) or not TAG_RE.match(tag):
                bad.append(
                    f"portal: tag {tag!r} must be 1-10 chars of "
                    f"[A-Za-z0-9._-].  TeleportWorld::Interact calls "
                    f"RequestText(..., 10), so a longer tag would PAIR but "
                    f"could never be re-typed; an empty tag pairs at random "
                    f"with the world's mod-location portals, which is the "
                    f"operator's one-way trip.")
            if p.get("pair_tag") != p.get("tag"):
                bad.append("portal: pair_tag must equal tag.  Pairing is exact "
                           "string equality; the field exists so a reader can "
                           "see the intent, not to allow a mismatch.")
            if "zdo_strings" in p and p["zdo_strings"].get("tag") != p.get("tag"):
                bad.append("portal: zdo_strings['tag'] must equal params.tag, "
                           "or the readable form and the payload disagree and "
                           "the write-time uniqueness check reads the wrong "
                           "one")

    elif op == "spawn_plan":
        if not SHA_RE.match(str(p.get("plan_sha256", ""))):
            bad.append("spawn_plan: plan_sha256 must be a sha256 hex digest of "
                       "the emitted plan file")
        anchor = p.get("anchor")
        if not isinstance(anchor, dict) or not {"x", "y", "z", "yaw"} <= set(anchor):
            bad.append("spawn_plan: anchor must be {x, y, z, yaw}")
        body = p.get("body")
        if body is not None:
            if not isinstance(body, dict) or "sha256" not in body or \
                    "filename" not in body:
                bad.append("spawn_plan: body must carry {filename, sha256} -- "
                           "the bodies are not vendored, so the digest is the "
                           "only thing that says the file on the next host is "
                           "the same building")
            elif not SHA_RE.match(str(body["sha256"])):
                bad.append("spawn_plan: body.sha256 must be a sha256 hex digest")

    elif op == "retire":
        if not _xyz(p.get("pos")):
            bad.append("retire: pos must be [x, y, z] in WORLD order -- "
                       "`deleteObjects -near` takes a cube about that point")
        if not isinstance(p.get("retires"), list) or not p["retires"] or \
                not all(isinstance(s, int) for s in p["retires"]):
            bad.append("retire: `retires` must name the seq numbers this "
                       "removes.  Without it the portal-pair invariant keeps "
                       "counting a deleted end and the ledger asserts "
                       "something true about the log and false about the "
                       "world.")
        if float(p.get("radius_m", 0)) > 20.0:
            bad.append("retire: radius_m > 20 -- `deleteObjects` echoes ONE "
                       "LINE PER DELETED OBJECT (155 bytes MEASURED), so a "
                       "wide cube is a question whose answer is long, and a "
                       "290 KB reply on the Unity main thread is what killed "
                       "the console sink.  Split it.")

    elif op == "observe":
        method = str(p.get("method", ""))
        if not method.startswith(("MEASURED", "INFERRED")):
            bad.append(f"observe: method {method!r} must start MEASURED or "
                       f"INFERRED.  A number presented as measured when it "
                       f"was inferred is how a 12 m bridge got planned over a "
                       f"40 m river.")
        if not str(p.get("tool", "")).strip():
            bad.append("observe: `tool` must name the script or command that "
                       "produced the value")

    return bad


def describe() -> str:
    """The schema, as text, for publishing to a sibling over `hub`."""
    out = [f"BUILD LEDGER SCHEMA v{SCHEMA_VERSION}", ""]
    for kind, spec in OPS.items():
        out.append(f"{kind}  [{'mutating' if spec.mutating else 'record-only'}, "
                   f"idempotent={spec.idempotent}]")
        out.append(f"    required: {', '.join(spec.required) or '(none)'}")
        out.append(f"    optional: {', '.join(spec.optional) or '(none)'}")
        out.append(f"    {spec.why}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(describe())
