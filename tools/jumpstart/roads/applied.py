#!/usr/bin/env python3
"""THE LIVE APPLIED SURFACE: what a player actually stands on, per zone, from
the ledger's own blobs.

WHY THIS EXISTS, AND IT IS THE INSTRUMENT THAT FOUND EVERY DEFECT IN THIS
FAMILY.  Every check this project ran before it read the surface answered a
question it was not asked:

  * `walkability` walks the CENTRELINE of the PLANNED profile.  It cannot see
    a wall beside the road, and it cannot see that the ground under the
    centreline is no longer the ground the plan asked for.
  * `edge_step_census` reads the surface THIS run composes -- its own
    compilers plus generated ground.  It cannot see a FOREIGN write that
    replaced this road's samples afterwards.

MEASURED with this instrument, by `RoadClear`: 89 of T4's 1,443 centreline
metres sit on samples that `SiteFinish` seq 286 (stenvik-beehive-1) overwrote
AFTER `RoadBuild` seq 213.  Sample (586, 954) holds road delta -4.470 while
the live union holds -1.571, so the applied surface jumps 45.05 -> 47.11 IN
ONE METRE, in the middle of the carriageway.  Worst applied-vs-planned
deviation per segment: T4 6.391 m, T13 4.400, T5 3.574, T3 2.113, T12 1.064.

HOW THE LIVE SURFACE IS COMPOSED, and each step is a measured property of the
game rather than a convention:

  1. A zone holds exactly ONE `_TerrainCompiler`
     (`Heightmap::GetAndCreateTerrainCompiler` returns the first it finds), so
     the live state of a zone is ONE compiler, not a stack.
  2. Every write into an already-written zone re-reads the prior blob and
     unions it PER SAMPLE INDEX.  A sample both writes claim goes to the LATER
     writer -- that is what "last writer wins per sample" means and it is what
     the live compiler holds.  So replaying the ledger's `terrain_write`
     records in order, letting later records overwrite per sample, reproduces
     the live compiler.
  3. `TerrainComp::ApplyToHeightmap` adds `m_levelDelta` (plus
     `m_smoothDelta`) to the GENERATED height at each 1 m sample.
  4. `Heightmap` then renders a mesh that interpolates LINEARLY between those
     samples, so the surface under an off-lattice point is the BILINEAR blend
     of the four samples around it and an unwritten sample contributes zero
     delta.

Composing it any other way -- nearest sample, planned profile, this run's own
compilers -- answers a different question.  Every one of those substitutions
has produced a false clear in this project.

READ-ONLY.  This module opens the ledger for reading, decodes blobs and
returns numbers.  It appends nothing and sends nothing.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(JUMPSTART / "terraform"))

import tcdata  # noqa: E402

# A terrain_write with this role is A ROAD.  Anything else that claims a
# sample is somebody else's property, and the ruling on a contested sample is
# that the PAD wins: it is a building's foundation and it must stay flat.
ROAD_ROLE = "road_segment"

# How far two claims' deltas may differ at one sample and still count as the
# SAME ground.  The ledger's own `FLATTEN_TOLERANCE_M`, and for the same
# reason: a bilinear blend of unwritten samples is exactly 0.0, so anything
# above float noise is a real difference.  One number, two spellings is how
# they drift apart, but `writer.py` must not import from `roads/`, so the
# value is restated with its provenance rather than shared.
AUTHOR_TOLERANCE_M = 0.01

# ---------------------------------------------------------------------------
# WHAT A UNION MUST DECLARE
# ---------------------------------------------------------------------------
def merged_declaration(priors_in_zone, owned_counts: dict) -> dict:
    """The two populations a zone union has to publish, and they are NOT the
    same set.

    `writer._zone_clobber_check` refuses a `terrain_write` into a zone that
    already holds a compiler unless the record NAMES the prior seqs in
    `entries[].merged_from`.  There are two candidate answers and only one of
    them is the guard's:

      WHAT DID I CARRY FORWARD   the per-sample `owner` walk -- the priors
                                 whose bytes are actually on the compiler
      WHAT AM I ABOUT TO DELETE  every prior claim in the zone, because the
                                 op deletes the compiler before it spawns

    `zone()` unions priors in FILE ORDER, so `owner[i]` is the LATEST claim on
    each sample and a prior wholly superseded by a later one owns nothing --
    while remaining a record whose compiler this op destroys.  MEASURED
    2026-09-22 on the four zones this session's refused writes named:

        zone      priors named by the guard   priors the owner walk sees
        (-14,30)             5                           1
        (0,0)               12                           1
        (-1,-1)             33                           1
        (10,17)             14                           1

    The owner walk names ONE seq in every one of them.  A record declaring
    that set is refused for the other four, eleven, thirty-two and thirteen --
    so the inheritance answer is the wrong population for this guard, and it
    is the answer two agents and a supervisor each proposed first.

    THE OTHER WRONG ANSWER IS WORSE AND IT IS WHY BOTH ARE RETURNED.
    Declaring the zone's set and saying nothing else asserts that this write
    carries claims it may carry none of: AN UNDECLARED UNION IS REFUSED AND A
    MIS-DECLARED UNION APPENDS.  So `merged_sample_counts` publishes the
    inheritance answer beside the guard's, and a 0 in it is the interesting
    row -- a prior this write supersedes entirely, visible instead of absent.

    This is the same split `zone()` already makes between `owner` and
    `author`, five lines above the function that needed it.

    PURE: seqs in, dict out, no world and no ledger, so the decision can be
    proved without a save.
    """
    in_zone = sorted({int(s) for s in priors_in_zone})
    counts = {s: int(owned_counts.get(s, 0)) for s in in_zone}
    return {
        "merged_from": in_zone,
        "merged_sample_counts": counts,
        "priors_with_no_sample_on_the_compiler": [s for s in in_zone
                                                  if counts[s] == 0],
        "merged_from_population": (
            f"every one of the {len(in_zone)} prior terrain_write claim(s) in "
            f"this zone, because the op deletes the zone's compiler before it "
            f"spawns; {sum(1 for v in counts.values() if v)} of them still "
            f"own a sample on it and "
            f"{sum(1 for v in counts.values() if not v)} are wholly "
            f"superseded and would be left unnamed by an inheritance-only "
            f"answer"),
    }


class Applied:
    """The live applied surface for as many zones as you ask about.

    Zones are decoded LAZILY and cached: the ledger holds 66 `terrain_write`
    records over ~470 zones and a segment needs 20-45 of them, so decoding the
    world costs ~20x what the question costs.
    """

    # THE WORLD IS NOT DEFAULTED AND THE REASON IS MEASURED.  This parameter
    # read `world: str = "Ulfsland"` and SEVENTEEN OF NINETEEN CALLSITES
    # PASSED NO WORLD -- ribbon.py's rim gate, lamps.py, regrade.py,
    # premise.py, cone.py, boardwalk.py, relight.py, crossings/plan.py,
    # settlements/{build,harbour_city,waterfront,terminus_camp}.py and
    # accept/sections/annulus.py among them.  MEASURED 2026-09-23 driving the
    # fresh seed Fresh42: stage 12 step 4, `ribbon.py build --segment
    # T1-portalhub-temple`, refused rc 7 with "948 step(s) CAUSED, worst
    # 6.8059 m" against ground owned by `road_T13_temple_harboursouth#5354`
    # and `road_T5_temple_dockshore#240` -- and Fresh42's ledger has 1,452
    # records, MAX SEQ 1,451, and ZERO `terrain_write` of any kind.  Seq 5354
    # is ULFSLAND'S.  The instrument reported "1786 terrain_write zone
    # entries, 479 zones claimed" for a world that has none: A FRESH WORLD'S
    # ROAD WAS JUDGED AGAINST ANOTHER WORLD'S GROUND, and the refusal it
    # produced was true of neither.  Same shape, same week, as
    # `LiveBuilder(world="Ulfsland")` with 36 of 40 callsites passing none.
    #
    # AN UNSTATED WORLD IS NOT A LITERAL, IT IS THE RUN'S OWN SCOPE.
    # `worldseed.world()` is the resolver eight modules in this tree already
    # read and `worldseed.apply()` PUBLISHES into `$VH_WORLD`, so by the time
    # any driver constructs this the run's world is on record.  Deferring to
    # it removes the second home for the name: `applied.py` no longer has an
    # opinion about which world is the world.  MEASURED: the Fresh42 run
    # above had `VH_WORLD=Fresh42` published the whole time -- the resolver
    # would have answered Fresh42 at every one of those seventeen callsites,
    # and only this module's private literal said Ulfsland.
    def __init__(self, world: str = "", actor: str = "RoadEmit",
                 led=None):
        # AN ALREADY-OPEN LEDGER MAY BE HANDED IN, and an emitter MUST hand
        # its own in.  `settlements/build.py::union_prior` reads this
        # instrument in the middle of a write, where a second `Ledger.open`
        # would be a second handle on the same append lock.  The alternative
        # a caller reaches for when this parameter is absent is a private
        # re-implementation of `zone()`, which is how this project got four
        # answers to "what is on the compiler" once already.
        from writer import Ledger  # noqa: PLC0415
        import worldseed  # noqa: PLC0415
        # STATED, RESOLVED, OR INHERITED FROM THE HANDLE -- and which of the
        # three is recorded, because an instrument that cannot say whose
        # ground it read is how this went unnoticed for nineteen callsites.
        self.world_source = ("stated" if world
                             else "led" if led is not None else "resolved")
        if not world and led is None:
            world = worldseed.world()
        self.world = world
        self.led = led if led is not None else Ledger.open(world, actor=actor)
        self.writes: list[dict] = []
        for line, rec in enumerate(self.led.records()):
            if rec.get("op") != "terrain_write":
                continue
            p = rec["params"]
            for e in p["entries"]:
                self.writes.append({
                    "file_line": line, "seq": rec["seq"],
                    "name": p.get("name"), "role": p.get("role"),
                    "actor": rec.get("actor"),
                    "zone": (int(e["zone"][0]), int(e["zone"][1])),
                    "sha": e["blob_sha256"]})
        self._zones: dict[tuple[int, int], dict] = {}

    # -- per zone ---------------------------------------------------------

    def zone(self, zx: int, zz: int) -> dict:
        """`{delta, modified, owner, author}` for one zone, unioned in FILE
        ORDER.

        FILE ORDER, not `seq`: the chain forked at file line 47 and carries
        duplicate seq labels 43-46, so `seq` is not a total order on this
        artefact and sorting by it would put two records in an arbitrary
        relative position.  The file is append-only and its line number is the
        only total order it has.

        TWO DIFFERENT QUESTIONS, TWO ARRAYS, and conflating them is a defect
        that cost a whole segment.

          `owner`  -- the LAST write whose blob HOLDS this sample.  That is
                      the right answer for "which record's bytes are on the
                      compiler", which is what a clobber check needs.
          `author` -- the last write whose blob CHANGED it.  That is the right
                      answer for "whose ground is this", because the blob a
                      claim sends is the UNION of everything its zones must
                      keep: a zone holds one `_TerrainCompiler` and
                      `terrain_write` deletes before it spawns, so every claim
                      carries its neighbours' samples forward verbatim and the
                      record then says it owns them.

        MEASURED, and it is why this exists: the portal hall's pad seq 801
        holds 2,085 samples and AUTHORED 1,349 of them -- all inside its
        37.4 x 37.4 m rectangle.  The other 736 are T1's and S1's carriageway,
        carried forward.  Under `owner`, `foreign_at` answers
        `portal-hall#801` over the whole of S1-portalhub-brgs2, so the
        rasteriser refused all 215 of its carriageway samples as somebody
        else's floor and stamped ZERO: the 30.6 m ramp a player walks off a
        portal could not be repaired at all.  Rewinding past this segment's
        own records cannot recover it -- the laundered copy is under the
        HALL's name, not the road's.  Under `author` the last claim to change
        those samples is the road's own write, which the rewind does drop, and
        the hall keeps every sample it actually laid.

        A write AUTHORS a sample when its value differs from the state the
        writes before it had composed, by more than `AUTHOR_TOLERANCE_M`.
        Where two claims agree to the centimetre the question is moot: the
        ground does not move, so it does not matter which of them is asked.
        """
        key = (int(zx), int(zz))
        got = self._zones.get(key)
        if got is not None:
            return got
        n = tcdata.SAMPLES
        delta = np.zeros(n, dtype=np.float64)
        modified = np.zeros(n, dtype=bool)
        owner = np.full(n, -1, dtype=np.int32)
        author = np.full(n, -1, dtype=np.int32)
        for w in sorted((w for w in self.writes if w["zone"] == key),
                        key=lambda w: w["file_line"]):
            blob = tcdata.parse(self.led.read_blob(w["sha"]))
            idx = self.writes.index(w)
            for i, (lvl, sm) in blob["heights"].items():
                v = lvl + sm
                if not modified[i] or abs(v - delta[i]) > AUTHOR_TOLERANCE_M:
                    author[i] = idx
                delta[i] = v
                modified[i] = True
                owner[i] = idx
        got = {"zone": key, "delta": delta, "modified": modified,
               "owner": owner, "author": author,
               "writes": [w for w in self.writes if w["zone"] == key]}
        self._zones[key] = got
        return got

    # -- the compiler restore, for the two emitters that rewrite a zone -----

    def restore_into(self, zx: int, zz: int, comp) -> dict:
        """Put the bytes that are ON THE COMPILER into `comp`, then PROVE it.

        A zone holds exactly one `_TerrainCompiler` and a terrain write
        deletes before it spawns, so any write into an already-written zone
        must carry the zone's current state forward or it DESTROYS it.  Two
        emitters do this - `settlements/build.py::union_prior` and
        `roads/ribbon.py` - and they had two implementations of it, one of
        which was wrong for eleven months of chain.

        THE QUESTION IS NOT A SCAN DIRECTION, IT IS `owner`.  `build.py` used
        to walk the priors in chain order taking a sample `if not already
        modified`, which makes the OLDEST claim that ever touched a sample win.
        MEASURED with no world on the 11 zones of five Ulfsland pad re-emits:
        oldest-wins disagrees with the live state on 7 of the 11 - zone (8,0)
        on 1,315 samples, (9,15) on 630, (8,14) on 208, (9,1) on 60, (9,14) on
        48 - and at sample (598, 967) in zone (9,15) it holds -3.916 m against
        the live +2.591 m, so a pad re-emit would have thrown away 6.507 m of
        `road_T4_meadhall_stathub`'s bed without mentioning it.

        `comp`'s OWN samples are never touched: this fills only where the
        current write wrote nothing, which is what "ours win where we wrote"
        means.  Returns the restore's own numbers, and RAISES if the result
        does not reproduce this instrument's own composition.
        """
        zc = self.zone(zx, zz)
        ours = {i for i in range(len(comp.modified_height))
                if comp.modified_height[i]}
        cache: dict[str, dict] = {}
        taken = 0
        # THE SEQS WHOSE CLAIMS THIS RESTORE ACTUALLY CARRIES FORWARD.  The
        # loop below already resolves the per-sample `owner` and reads that
        # owner's blob; the seq is right there on `self.writes[oi]` and used
        # to be dropped on the floor.
        #
        # AND THE GUARD FIVE LINES AWAY WANTS EXACTLY THIS SET.
        # `writer._zone_clobber_check` refuses any terrain_write into a zone
        # that already holds a compiler unless the record NAMES the prior
        # seqs in `entries[].merged_from`.  MEASURED 2026-09-22 on
        # `regrade.py`'s first live build: zone (-14,30), 54 samples written
        # and 1,061 INHERITED from seq 495/516/3335/3340/5372, every one of
        # them re-checked against the live applied surface by
        # `assert_restores_current` -- and the append was refused for not
        # naming the five seqs this function had just read the blobs of.
        # THE UNION WAS PERFORMED AND NOT DECLARED.
        #
        # It is the same return value's second half: `merge_policy` was
        # already fixed here from prose to the schema's enum after wt-south
        # seq 5358 failed on exactly its three zones that HAD a prior claim.
        # One half of one guard's demand was learned and the other was not.
        #
        # `ribbon.py` and `portal_hall.py` pass their own literals and are
        # untouched by this key existing.
        merged: dict[int, int] = {}
        for i in range(tcdata.SAMPLES):
            if i in ours or not zc["modified"][i]:
                continue
            oi = int(zc["owner"][i])
            if oi < 0:
                continue
            sha = self.writes[oi]["sha"]
            old = cache.get(sha)
            if old is None:
                old = tcdata.parse(self.led.read_blob(sha))
                cache[sha] = old
            hit = old["heights"].get(i)
            if hit is None:
                continue
            comp.modified_height[i] = True
            comp.level_delta[i], comp.smooth_delta[i] = hit
            taken += 1
            merged[int(self.writes[oi]["seq"])] = \
                merged.get(int(self.writes[oi]["seq"]), 0) + 1
            paint = old.get("paints", {}).get(i)
            if paint is not None and not comp.modified_paint[i]:
                comp.modified_paint[i] = True
                comp.paint[i] = paint
        checked = self.assert_restores_current(zx, zz, comp, ours)
        return {"samples_inherited": taken, "samples_written_by_us": len(ours),
                "samples_checked_against_applied": checked,
                **merged_declaration(
                    [w["seq"] for w in self.writes
                     if w["zone"] == (int(zx), int(zz))], merged),
                # THE SCHEMA FIELD IS AN ENUM, NOT A SENTENCE.  What this
                # loop does IS `union`: it merges per SAMPLE INDEX and ours
                # win only where we wrote -- it never overwrites a zone
                # wholesale, which is what `replace` declares.  Returning the
                # prose here put a paragraph in the enum slot, and
                # `schema.py` refused the append with
                # "entries[i].merge_policy must be 'union' or 'replace'" --
                # MEASURED on the first `build.py pad` emit ever attempted
                # (wt-south, seq 5358), which failed on 3 of its 4 entries:
                # exactly the three zones that HAD a prior claim, because the
                # check only fires where `merged_from` is present.  So every
                # caller that unions through this function was unable to
                # write a zone somebody had already written -- the one case
                # the union exists for.  `ribbon.py` and `portal_hall.py`
                # never hit it only because they hardcode the literal.
                "merge_policy": "union",
                "merge_policy_why": ("restore the per-sample OWNER - the "
                                     "claim whose bytes are on the compiler "
                                     "- in FILE ORDER; ours win only where "
                                     "we wrote")}

    def assert_restores_current(self, zx: int, zz: int, comp,
                                ours: set[int]) -> int:
        """Every RESTORED sample must equal the live applied delta.

        THE CHECK THAT WOULD HAVE CAUGHT THE REVERSION.  It is asked of the
        finished compiler against this instrument's own composition rather
        than of the loop that filled it, so a future change to any such loop
        cannot pass it by construction - and `roads/ribbon.py`'s union, which
        is correct today, has no such check and was itself wrong once
        ("it stopped at the first prior blob holding a sample, which on a zone
        with five prior writes is the OLDEST").

        `ApplyToHeightmap` adds `m_levelDelta + m_smoothDelta`, so that sum is
        the quantity compared - the same one `zone()` composes.

        IT RAISES RATHER THAN REPORTING.  The damage is invisible afterwards:
        the reverted samples lie OUTSIDE the writing pad's own rectangle, so
        its delta gate, its sample verdict and its own `expect` postcondition
        all pass over a zone whose road has just been un-graded.
        """
        zc = self.zone(zx, zz)
        wrong = []
        checked = 0
        for i in range(tcdata.SAMPLES):
            if i in ours or not zc["modified"][i]:
                continue
            want = float(zc["delta"][i])
            got = (comp.level_delta[i] + comp.smooth_delta[i]
                   if comp.modified_height[i] else None)
            checked += 1
            if got is None or abs(got - want) > AUTHOR_TOLERANCE_M:
                wrong.append((i, want, got))
        if not wrong:
            return checked
        cx, cz = tcdata.zone_centre(zx, zz)
        shown = []
        for i, want, got in sorted(
                wrong, key=lambda t: -abs((t[2] or 0.0) - t[1]))[:5]:
            iy, ix = divmod(i, tcdata.PITCH)
            oi = int(zc["owner"][i])
            who = self.writes[oi] if oi >= 0 else None
            shown.append(
                f"sample {i} at ({cx + ix - 32:g},{cz + iy - 32:g}): the "
                f"compiler holds delta {want:+.3f} m from "
                + (f"{who['name']} (file line {who['file_line'] + 1}, seq "
                   f"{who['seq']})" if who else "an unnamed claim")
                + ", this restore would write "
                + ("NOTHING" if got is None else f"{got:+.3f} m"))
        raise SystemExit(
            f"REFUSED: the compiler restore for zone {(zx, zz)} does not "
            f"reproduce the zone's live applied surface at {len(wrong)} of "
            f"{checked} restored samples, worst "
            f"{max(abs((t[2] or 0.0) - t[1]) for t in wrong):.3f} m. Writing "
            f"this blob would REVERT a write that nothing in this record "
            f"mentions. {'; '.join(shown)}")

    def sample(self, sx: int, sz: int, *,
               by: str = "author") -> tuple[float, dict | None]:
        """`(delta, the write whose ground this is)` at an INTEGER lattice
        sample.

        `by="author"` -- the last claim that CHANGED this sample.  The default,
        because every caller of this method is asking an ownership question
        ("may I write here", "whose road is this"), and the union means the
        last claim to HOLD a sample is routinely not the one that put it
        there.
        `by="owner"` -- the last claim whose blob holds it, for the clobber
        question of which record's bytes are on the compiler.
        """
        zx, zz = tcdata.zone_of(sx, sz)
        zc = self.zone(zx, zz)
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return 0.0, None
        k = gy * tcdata.PITCH + gx
        if not zc["modified"][k]:
            return 0.0, None
        o = int(zc[by][k])
        return float(zc["delta"][k]), (self.writes[o] if o >= 0 else None)

    # -- off-lattice, the way Heightmap renders it ------------------------

    def delta_at(self, x: float, z: float) -> float:
        """Bilinear blend of the four surrounding samples' live deltas."""
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        total = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            if w == 0.0:
                continue
            d, _ = self.sample(x0 + dx, z0 + dz)
            total += w * d
        return total

    def holders(self, sx: int, sz: int) -> list[dict]:
        """EVERY write whose zone lattice holds the sample at `(sx, sz)`.

        `sample()` resolves a sample to ONE zone, `tcdata.zone_of`'s, and for a
        height that is right: the engine renders each zone's own mesh.  For
        OWNERSHIP it is wrong, because a zone's lattice is 65 x 65 over a 64 m
        zone -- the boundary row is SHARED -- so one world sample sits in two
        compilers and `zone_of` picks one of them.

        MEASURED, and this is a hole in the clobber detector that has nothing
        to do with the union: sample (498, 96) is owned by `wt-south` seq 373,
        a site_pad, in zone (8,1); `zone_of` says (8,2), where nobody wrote it,
        so `sample()` answers None and `foreign_at` answers None and a road is
        free to author a watchtower's floor.  At (522, 480) it is worse -- zone
        (8,7) holds it as `tree-sth-2` seq 669's pad and zone (8,8) holds it as
        a road, and `zone_of` picks the road.  Two compilers then disagree
        about one square metre of ground.
        """
        out: list[dict] = []
        zx0, zz0 = tcdata.zone_of(sx, sz)
        for dzx in (-1, 0, 1):
            for dzz in (-1, 0, 1):
                zx, zz = zx0 + dzx, zz0 + dzz
                cx, cz = tcdata.zone_centre(zx, zz)
                gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
                if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
                    continue
                zc = self.zone(zx, zz)
                k = gy * tcdata.PITCH + gx
                if not zc["modified"][k]:
                    continue
                # THE AUTHOR, not the holder: every zone that carries this
                # sample forward would otherwise read as a claim on it, and
                # the union means most of them do carry it.  See `zone`.
                o = int(zc["author"][k])
                w = self.writes[o] if o >= 0 else None
                if w is not None and w not in out:
                    out.append(w)
        return out

    def owners_at(self, x: float, z: float) -> list[dict]:
        """Every write that owns a sample the mesh under (x, z) is BLENDED
        FROM -- and only those.

        THE FOUR CORNERS OF THE CELL, WEIGHTED, NOT THE FOUR NEIGHBOURS.  The
        surface at (x, z) is `delta_at`'s bilinear blend, and `delta_at`
        already skips a corner whose weight is 0.0 because it contributes
        nothing to the height.  This method did not, and the two answers
        disagreed about which square metre is under a point: asked at an EXACT
        LATTICE SAMPLE (tx == tz == 0) it walked (sx, sz), (sx+1, sz),
        (sx, sz+1) and (sx+1, sz+1) -- three samples a whole metre away, whose
        blend weight is zero and whose height the player standing on (sx, sz)
        never touches.

        MEASURED, and it is the unwritten annulus.  Sample (-299, 214) beside
        the portal hall is AUTHORED by `road_S1_portalhub_brgs2` seq 417 at
        -1.578 m.  Its east neighbour (-298, 214) is the hall's pad,
        `portal-hall` seq 801.  With the zero-weight corners walked,
        `foreign_at(-299.0, 214.0)` answered `portal-hall#801`, so
        `ribbon.stamp`'s `refusal` returned "foreign" and every road repair
        since the hall was laid has SKIPPED that sample -- leaving a 1.75 m
        pit one metre wide inside the apron, walled on both sides after T1's
        rejoin raised its west neighbour, and 0 of 28 portal destinations
        reachable.  The pad never wrote it and the road was forbidden to: a
        one-sample annulus on the low side of EVERY pad on this world.

        A claim on a sample a point is not standing on is not a claim on that
        point.  The zone expansion in `holders` stays exactly as it is -- a
        boundary sample really does live in two compilers, and that is a
        different question from which samples are under a foot.
        """
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        out = []
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            if w == 0.0:
                continue
            for hit in self.holders(x0 + dx, z0 + dz):
                if hit not in out:
                    out.append(hit)
        return out

    def foreign_at(self, x: float, z: float) -> dict | None:
        """The NON-road write owning ground under (x, z), if any.

        This is the detector for the clobber defect and it is deliberately
        blind to radii: a pad's written footprint is not its nominal
        `pad_radius_m`, and the T4 clobber was measured OUTSIDE the road's own
        pad keep-out.  Ownership is read from the blobs, not inferred from a
        circle -- from every ZONE that holds the sample, because a boundary
        sample lives in two of them, and from every sample the point is
        BLENDED FROM, because a corner with zero weight is not under the
        point (see `owners_at`).
        """
        for w in self.owners_at(x, z):
            if w["role"] != ROAD_ROLE:
                return w
        return None

    def surface_at(self, patches: dict, x: float, z: float):
        """`(applied_y, generated_y, live_delta)` or None if not covered.

        `patches` is a `{"z_zx_zz": Patch}` map of GENERATED heights, exactly
        what `ribbon.zone_patches` returns.

        A ZERO-WEIGHT CORNER MUST NOT DECIDE COVERAGE.  This walked all four
        bilinear corners and returned None if ANY of their zones was absent,
        and only THEN checked the weight - so an INTEGER query, whose three
        `+1` corners carry weight exactly 0 and contribute nothing to the
        answer, was refused whenever one of them fell in the next zone.

        MEASURED 2026-09-22 on Ulfsland b608a61b, driving the pad re-emit
        gate over `stenvik-hut-1`: the pad is 10.4 x 10.4 m at (538.3, 897.2)
        and lies entirely inside zone (8,14), which its own patch set holds -
        yet 11 of its 121 lattice vertices came back None, every one of them
        the x = 543 column, whose weight-0 neighbour at x = 544 is row 0 of
        zone (9,14).  The value this returns for those 11 is unchanged; they
        simply stop being refused.

        This is the same defect `annulus` already refuses in `owners_at` -
        "zero-weight corners made the pad claim it", the portal-hall apron -
        and the same one `relight.zones_around` works around by over-fetching
        a ring of zones.  Fixed at the reader, so the workaround is no longer
        the only thing standing between a caller and a silent None.
        """
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        gen = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            if not w:
                continue
            sx, sz = x0 + dx, z0 + dz
            zx, zz = tcdata.zone_of(sx, sz)
            patch = patches.get(f"z_{zx}_{zz}")
            if patch is None:
                return None
            cx, cz = tcdata.zone_centre(zx, zz)
            gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
            if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
                return None
            gen += w * float(patch.at(gy, gx))
        d = self.delta_at(x, z)
        return gen + d, gen, d


def generated_at(patches: dict, x: float, z: float) -> float | None:
    """Bilinear GENERATED height, no deltas.  None when uncovered.

    Same rule as `Applied.surface_at`: a corner with weight 0 contributes
    nothing to the answer and may not decide that there is no answer.
    """
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    gen = 0.0
    for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                      (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
        if not w:
            continue
        sx, sz = x0 + dx, z0 + dz
        zx, zz = tcdata.zone_of(sx, sz)
        patch = patches.get(f"z_{zx}_{zz}")
        if patch is None:
            return None
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return None
        gen += w * float(patch.at(gy, gx))
    return gen
