#!/usr/bin/env python3
"""IS THIS WRITE A RE-EMIT, OR DOES IT MOVE GROUND? DERIVED, NEVER DECLARED.

WHAT THIS EXISTS TO CORRECT.  A gate introduced after an artefact refuses that
artefact for conditions it did not create.  MEASURED 2026-09-21 on two
instruments at once, and the two faces are the same defect:

  `T1Exec`, on the junction gate: 19 impassable pairs between two RECORDED
    profiles, both of them in `roads/segments.yaml` today and both in the
    ground today.  Not one of the five segments had a single `profile_y`
    station changed by the campaign.  Re-emitting writes the heights ALREADY
    IN THE GROUND and moves each of those steps by 0.000 m -- and the gate
    refuses them, because the gate is newer than the roads.
  `OpenNorthChain`, on the same instrument: 277 pairs refused against five
    roads that were never built at all.

One asks WHAT COUNTS AS A CHANGE, the other WHO COUNTS AS A COUNTERPARTY, and
neither is about the repair.  Both are the gate judging a STATE when the only
thing it is entitled to judge is a DELTA.

  THE GATE ASKS          "is this state acceptable"
  THE ANSWERABLE QUESTION IS  "did THIS WRITE make it worse"

That is the night's own absent-versus-empty rule pointed at TIME instead of at
data: a count of zero that cannot say whether anyone looked, a census blank
where a zone was generated after it, and a refusal that cannot say whether the
condition predates the writer.

WHY IT MATTERS MORE THAN IT LOOKS.  `SurveyNodes` drew the line: a newer gate
against a DERIVED artefact is a re-run -- regenerate, thirty seconds, nothing
in the world moves.  Against a WRITTEN one it is a claim about ground somebody
already stood on.  `no_crossing` took the roads package down and cost nothing;
this gate stands in front of 93,353 lines of standing ground.  And `SeedField`
named the failure mode if it is left: every gate installed from now on freezes
the world at the moment of its installation, and the only way past is a waiver
-- which is the mechanism by which waivers become routine, and a routine waiver
is how 42 standing bodies ended up under a carriageway.

THREE CONSTRAINTS, ALL LOAD-BEARING, NONE OF THEM NEGOTIABLE:

  DERIVED, NEVER DECLARED.  A `--re-emit` flag a caller asserts is `CLEARABLE`
    becoming an authorisation -- a classification quietly turning into a
    permission, which is the trap this project has now hit four times.  The
    answer is COMPUTED here by comparing the emission to the record, and a
    caller cannot assert it.

  IDENTITY, NEVER CLOSENESS.  Byte-identity is decidable; "close enough to
    count as unchanged" is a tolerance standing in for a population.  MEASURED
    tonight, and it is why this is not a band: `PIN_EXACT_M = 0.0005` is HALF
    `segments.yaml`'s 3-decimal quantum, so a band around a rounded value can
    still resolve one full quantum away -- 0.0500 -> 0.0010 -> 0.000000 took
    two agents and four hours.  A TIGHTENED BAND IS NOT A PIN, and a tolerance
    here would be the same mistake with a different name.

  NOTHING IS WAIVED -- THE QUESTION IS CORRECTED.  A re-emit does not make a
    pre-existing impassable pair acceptable.  It makes it NOT THIS WRITE'S.
    The pairs stay enumerated, stay reported, and stay REFUSED for anyone who
    moves them.  `carriageway_unchanged` is void the moment any station moves, which makes
    it a CERTIFICATION THAT NOTHING MOVED rather than permission for movement.

SINGLE POPULATION, BY CONSTRUCTION.  Everything here is decidable from the plan
file the run loaded and the profile it is about to emit.  No second world, no
prior save, no reference artefact -- which is the constraint `OpenNorthChain`'s
retired superset check bought: a check needing a population a fresh seed does
not have passes vacuously on every world that matters.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


# The plan file stores profile heights rounded to three decimals, so the
# recorded value and a faithfully re-emitted one are the SAME FLOAT and compare
# exactly.  This is stated rather than assumed because the whole check rests on
# it: if a stage ever re-derives a height instead of carrying it, the values
# stop being equal and this correctly reports movement.
QUANTUM_M = 0.001


class Moved(dict):
    """One station whose emitted height is not the recorded one."""


class NotMeasured(dict):
    """AN ABSENT READING, AND IT SAYS SO. Falsy on purpose.

    A plain `None` here is a null that does not say which kind of null it is,
    and the failure it enables is not a caller who checks but one who does not:
    an absent row read as agreement prints "0 moved / 0.000000", which is
    indistinguishable from a measured zero.  MEASURED tonight, twice, by two
    agents who caught it only because they looked at `unmatched_xz`: a row that
    matched NOTHING and a row that matched EVERYTHING print the same two
    numbers.

    So this is falsy -- `if not e:` works -- and `compare_to_ground` REFUSES
    it outright rather than returning a clean-looking verdict over an empty
    comparison.
    """

    def __bool__(self) -> bool:
        return False


def snapshot(seg: dict) -> list[float]:
    """THE RECORD, taken at load, before anything can mutate it.

    `ribbon.py` mutates `seg["profile_y"]` in place -- `solve_approach` rebinds
    it per attempt and `grade_into_foreign` rewrites it -- so a comparison made
    at emission against `seg["profile_y"]` would be comparing the emission with
    itself and would report every write as a re-emit.  That is the failure this
    function exists to make impossible: the snapshot MUST be taken from the
    document as loaded.
    """
    return [float(v) for v in seg["profile_y"]]


PLAN_FILE = "plan-file: roads/segments.yaml profile_y as loaded"
LAST_EMISSION = ("last-emission: the last terrain_write in the ledger chain "
                 "for this segment, params.profile.nodes")


def last_emission(ledger_path, params_name: str) -> dict | None:
    """THE LAST THING ACTUALLY WRITTEN for a segment, out of the chain itself.

    WHY THIS REFERENCE EXISTS AND THE PLAN FILE IS NOT ENOUGH.  Comparing an
    emission against the PLAN FILE answers "did this run change the plan's
    heights".  Comparing it against THE LAST EMISSION answers "will this write
    move ground".  THEY ARE DIFFERENT QUESTIONS AND THEY GIVE DIFFERENT
    ANSWERS: measured 2026-09-22 across the whole chain, several segments'
    plans disagreed with the ground they were about to be re-emitted over, and
    a signed authorisation rested on the first question while its premise was a
    claim about the second.

    NO SEGMENT, SEQ OR MAGNITUDE IS QUOTED HERE, AND THAT IS THE POINT.  An
    earlier version of this docstring carried a worked table of live rows; the
    chain moved under it within the hour.  A docstring that cites live data is
    a transcription with no producer -- the defect this module exists to
    refuse, in the module's own prose.  The numbers belong in the run that
    takes them.  ASK THE CHAIN.

    MATCHED BY XZ, NEVER BY INDEX.  `ribbon.py` emits `if not brg`, so bridge
    stations are absent from the written profile and an index comparison would
    silently misalign every station after the first bridge -- reporting
    movement that is really an offset, which is the same shape as comparing two
    populations that are not the same set.

    Returns None when the chain holds no emission for this segment, and the
    caller MUST treat that as NOT MEASURED rather than as agreement.  A caller
    that reads None as agreement prints a CLEAN ZERO -- no movement, no worst case -- FROM AN ABSENT ROW
    -- the absent-versus-empty failure, produced by the module written to
    refuse it.

    AN EXAMPLE THAT USED TO STAND HERE WAS FALSE AND IT IS STRUCK.  It named
    two segments as having no profile-carrying write in the chain.  Both had
    six.  That was my own error, retracted in the open within minutes and then
    LEFT IN THE DOCSTRING, where another agent found it while grounding a
    console write -- a correction that landed in a chat log and not in the
    code.  A reader trusting it would have treated a MEASURED segment as
    unmeasured, and one of the two was the worst diverger in the network, so
    the sentence hid the row most worth reading.

    NO SEGMENT ID, SEQ OR MAGNITUDE APPEARS IN THIS DOCSTRING, AND THE
    SELFTEST ENFORCES IT ABSOLUTELY -- not "after a retraction marker", which
    would permit the next stale example to be appended below the last one.
    Live data in a docstring is a transcription with no producer: it goes
    stale the moment the chain moves, and a stale docstring is
    indistinguishable from one that was always wrong.  ASK THE CHAIN.
    """
    import json                                             # noqa: PLC0415
    from pathlib import Path                                # noqa: PLC0415

    found = None
    for line in Path(ledger_path).read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("op") != "terrain_write":
            continue
        params = r.get("params") or {}
        if params.get("name") != params_name:
            continue
        nodes = (params.get("profile") or {}).get("nodes")
        if nodes:
            found = {"seq": r.get("seq"), "actor": r.get("actor"),
                     "ts": r.get("ts"), "nodes": nodes}
    if found is None:
        # A NULL THAT SAYS WHICH KIND OF NULL IT IS.  `NotMeasured` is FALSY,
        # so `if not e:` works, and `compare_to_ground` REFUSES it outright --
        # because the failure this guards is not a caller who checks, it is a
        # caller who does not, and gets "0 moved / 0.000000" out of a row
        # nothing ever read.
        return NotMeasured(
            measured=False, params_name=params_name,
            why=(f"no terrain_write in {ledger_path} carries a profile for "
                 f"{params_name!r}. THIS IS NOT AGREEMENT: nothing was "
                 f"compared. Treating it as zero movement would be an absent "
                 f"row read as a measurement."))
    return found


def compare_to_ground(emission_nodes, seg: dict) -> dict:
    """`(agrees, moved[])` between a written profile and a plan record, by xz.

    `emission_nodes` is `[[x, z, y], ...]` as the ledger stores it.  Stations
    whose xz is not in the record are reported as `unmatched_xz` and are NOT
    counted as agreement -- an unmatched station is one this comparison could
    not speak for, which is not the same claim as one it found equal.
    ZERO MATCHES AND ZERO DIFFERENCES PRINT THE SAME TWO NUMBERS, so an empty
    or absent emission is REFUSED here rather than returned as agreement.
    MEASURED tonight by two agents independently: one read the ledger's
    `[x, z, y]` as `[x, y, z]`, matched 0 of 244, and got a clean-looking
    "moved 0, worst 0.000000"; the other would have printed the same from a
    `NotMeasured` row. Both caught it only by looking at `unmatched_xz`, which
    is exactly the field a hurried caller does not look at.
    """
    if not emission_nodes:
        raise SystemExit(
            "REFUSING: no emission to compare. "
            + (emission_nodes.get("why", "") if isinstance(emission_nodes, dict)
               else "the emission is empty")
            + " An absent comparison is NOT agreement, and returning zero "
              "movement here would be an absent row read as a measurement.")
    rec = {(round(float(n[0]), 2), round(float(n[1]), 2)): float(y)
           for n, y in zip(seg["nodes"], seg["profile_y"])}
    moved: list[Moved] = []
    identical = unmatched = 0
    for x, z, y in emission_nodes:
        key = (round(float(x), 2), round(float(z), 2))
        if key not in rec:
            unmatched += 1
            continue
        if float(y) != rec[key]:
            moved.append(Moved(xz=[key[0], key[1]], ground_y=float(y),
                               record_y=rec[key],
                               delta_m=round(rec[key] - float(y), 6)))
        else:
            identical += 1
    return {
        "agrees": not moved and not unmatched,
        "reference": LAST_EMISSION,
        "emitted": len(emission_nodes),
        "identical": identical,
        "moved_count": len(moved),
        "unmatched_xz": unmatched,
        "moved": moved[:40],
        "worst_move_m": (max(abs(m["delta_m"]) for m in moved) if moved
                         else 0.0),
        "means": ("delta_m is what a re-emit of the RECORD would move the "
                  "GROUND by. Zero here is the only state in which a re-emit "
                  "moves nothing."),
    }


def compare(recorded: Sequence[float], emitted: Sequence[float],
            nodes: Sequence[Sequence[float]] | None = None) -> dict:
    """CARRIAGEWAY delta for one segment, by EXACT equality. Nothing else.

    A length change is movement by definition -- a station added or dropped is
    ground that was not written the same way -- and it is reported as such
    rather than compared pairwise off the end.

    THIS FUNCTION IS ABOUT THE CENTRELINE AND IT MUST NEVER BE READ AS MORE.
    `profile_y` is ONE input to the rasteriser; THE BATTER is the other, and it
    is computed from cut/fill against terrain THAT MAY ITSELF HAVE MOVED since
    the record was written -- by an adjacent segment's write, or by a zone
    generated after the fact.  So a write whose every station matches the
    record CAN STILL MOVE GROUND OFF THE CENTRELINE, which is precisely the
    half the junction gate does not look at and the flatten guard does.

    `OpenNorthChain` named this in its own idiom and that is why it is in the
    return value rather than in a comment: "I wrote `BAND_M = 24.0` over a
    population 54% beyond it; a `re_emit: true` that reads as 'nothing moved'
    when it means 'no station moved' is the same overstatement with a different
    noun."  The key is therefore `carriageway_unchanged`, never `is_reemit`,
    and `batter_delta_m` is present and explicitly NOT MEASURED HERE so a
    reader cannot take silence for zero -- absent is not empty, for the fifth
    time tonight.
    """
    if not len(recorded) or not len(emitted):
        raise SystemExit(
            f"REFUSING: nothing to compare -- recorded {len(recorded)} "
            f"stations, emitted {len(emitted)}. `moved_count: 0` over an empty "
            f"comparison is byte-identical to a measured zero, and this "
            f"function will not produce one. THE GENERAL RULE, measured three "
            f"separate ways tonight: ANY COMPARISON REPORTING 'N MOVED, WORST "
            f"X' MUST REPORT ITS MATCH COUNT ALONGSIDE, OR A ZERO IS "
            f"UNREADABLE -- it means 'measured and identical' or 'compared "
            f"nothing' and the output cannot tell you which.")
    moved: list[Moved] = []
    n = min(len(recorded), len(emitted))
    for i in range(n):
        if float(recorded[i]) != float(emitted[i]):
            m = Moved(station=i,
                      recorded_y=float(recorded[i]),
                      emitted_y=float(emitted[i]),
                      delta_m=round(float(emitted[i]) - float(recorded[i]), 6))
            if nodes is not None and i < len(nodes):
                m["xz"] = [round(float(nodes[i][0]), 2),
                           round(float(nodes[i][1]), 2)]
            moved.append(m)
    length_changed = len(recorded) != len(emitted)
    return {
        "carriageway_unchanged": not moved and not length_changed,
        "reference": PLAN_FILE,
        "reference_note": "this answers 'did this run change the plan's "
                          "heights'. It does NOT answer 'will this write move "
                          "ground' -- for that the reference is the last "
                          "emission in the chain; see compare_to_ground().",
        "scope": "CARRIAGEWAY ONLY: every station's profile_y. This says "
                 "NOTHING about the batter, which is computed against terrain "
                 "that may have moved since the record was written.",
        "batter_delta_m": None,
        "batter_delta_note": "NOT MEASURED HERE -- measure it separately and "
                             "report it beside this verdict. A null here is "
                             "'not looked at', never 'zero'.",
        "stations": len(emitted),
        "stations_recorded": len(recorded),
        "length_changed": length_changed,
        "moved_count": len(moved),
        # Bounded for the report, but `moved_count` is the whole population so
        # a reader can never mistake the printed list for the finding.
        "moved": moved[:40],
        "worst_move_m": (max(abs(m["delta_m"]) for m in moved) if moved
                         else 0.0),
        "method": ("exact equality of every station's profile_y against the "
                   "plan file as loaded; no tolerance, no rounding"),
    }


def divergence(seg: dict, emission_nodes) -> dict:
    """WHOSE DIVERGENCE IS IT -- the PLAN'S, or the EMITTER'S?

    `compare_to_ground` says a plan and the ground disagree at N stations.
    That is ONE number over TWO POPULATIONS and they need opposite handling:

      DELIBERATE  a station inside a recorded `reconciled[].at` +- `taper_m`.
                  The plan was changed on purpose and the ground has not
                  caught up: re-emitting is the repair.
      DRIFT       everything else.  `ribbon.py::solve_approach` and
                  `grade_into_foreign` REWRITE `seg["profile_y"]` at emission
                  time -- `snapshot()`'s own docstring says so -- and nothing
                  writes the result back into the plan file.  The ground holds
                  a height the plan has never seen, and the plan holds one the
                  world has never had.

    WHY THIS IS NOT BOOKKEEPING.  Every PLAN-TIME gate reads `profile_y`:
    `junction.build_gate`, `junction.reconcile`, `deviate.splice`'s pins.  On
    a drifted station each of them is measuring a height that is not in the
    world, and the refusals it produces cannot be reconciled -- the proposal
    would move ground that is already where the proposal wants it.  MEASURED
    on Ulfsland 2026-09-22, T12-wtspawn-temple: 16 drifted stations, worst
    3.009 m, producing 10 of the segment's 22 junction refusals INCLUDING a
    3.180 m disagreement with the wt-spawn pad whose ground agrees with that
    pad to 0.000 m.  `--reconcile --at` answered [] there, which reads as
    "already reconciled" and means "there is nothing here to reconcile,
    because the thing you measured is not the ground".

    NETWORK-WIDE, same measurement, same day: 137 drifted stations across 9
    of the 25 built segments, against 43 deliberate ones across 3.  Drift is
    the larger population and it is the one nobody was tracking.

    MATCHED BY XZ, NEVER BY INDEX, and the match count travels, for the same
    reason `compare_to_ground` carries it: a row that matched NOTHING and a
    row that matched EVERYTHING print the same two numbers.

    THE TAPER IS ARC LENGTH ALONG THIS SEGMENT'S OWN NODES, not a radius.  A
    road that doubles back -- T12 does, twice -- passes within a few metres of
    a reconciliation anchor at a station 60 m away along the carriageway, and
    a euclidean window would claim that station for a taper that never touched
    it.  `junction.reconcile` re-grades ALONG the road and this reads it back
    the same way.
    """
    import math                                             # noqa: PLC0415

    if not emission_nodes:
        raise SystemExit(
            "REFUSING: no emission to classify against. "
            + (emission_nodes.get("why", "") if isinstance(emission_nodes, dict)
               else "the emission is empty")
            + " An absent comparison is NOT agreement.")
    nodes = [(round(float(a), 2), round(float(b), 2)) for a, b in seg["nodes"]]
    prof = [float(v) for v in seg["profile_y"]]
    ground = {(round(float(x), 2), round(float(z), 2)): float(y)
              for x, z, y in emission_nodes}
    arc = [0.0]
    for k in range(len(nodes) - 1):
        arc.append(arc[-1] + math.hypot(nodes[k + 1][0] - nodes[k][0],
                                        nodes[k + 1][1] - nodes[k][1]))
    covered: set[int] = set()
    anchors = []
    for rc in (seg.get("reconciled") or ()):
        ax, az = rc["at"]
        taper = float(rc["taper_m"])
        ci = min(range(len(nodes)),
                 key=lambda i: math.hypot(nodes[i][0] - ax, nodes[i][1] - az))
        win = [i for i in range(len(nodes)) if abs(arc[i] - arc[ci]) <= taper]
        covered |= set(win)
        anchors.append({"at": [ax, az], "taper_m": taper, "centre_station": ci,
                        "stations": [min(win), max(win)] if win else None})
    matched = deliberate = drift = 0
    unmatched = 0
    d_rows: list[Moved] = []
    l_rows: list[Moved] = []
    for i, k in enumerate(nodes):
        g = ground.get(k)
        if g is None:
            unmatched += 1
            continue
        matched += 1
        if prof[i] == g:
            continue
        row = Moved(station=i, xz=[k[0], k[1]], ground_y=g, record_y=prof[i],
                    delta_m=round(prof[i] - g, 6))
        if i in covered:
            deliberate += 1
            l_rows.append(row)
        else:
            drift += 1
            d_rows.append(row)
    d_rows.sort(key=lambda r: -abs(r["delta_m"]))
    l_rows.sort(key=lambda r: -abs(r["delta_m"]))
    return {
        "segment": seg.get("id"),
        "stations": len(nodes),
        "matched_xz": matched,
        "unmatched_xz": unmatched,
        "reconciliation_records": len(anchors),
        "reconciliation_windows": anchors,
        "deliberate_count": deliberate,
        "deliberate": l_rows[:40],
        "worst_deliberate_m": (max(abs(r["delta_m"]) for r in l_rows)
                               if l_rows else 0.0),
        "drift_count": drift,
        "drift": d_rows[:40],
        "worst_drift_m": (max(abs(r["delta_m"]) for r in d_rows)
                          if d_rows else 0.0),
        "drift_xz": [r["xz"] for r in d_rows],
        "reference": LAST_EMISSION,
        "method": ("every station matched to the last emission BY XZ; a "
                   "divergent station is DELIBERATE when it lies within a "
                   "recorded `reconciled[].taper_m` of that record's anchor "
                   "MEASURED AS ARC LENGTH along this segment's own nodes, "
                   "and is EMIT-TIME DRIFT otherwise. The match count is "
                   "reported because a comparison that matched nothing and "
                   "one that matched everything print the same two numbers."),
        "tool": "roads/reemit.py::divergence",
    }


def classify(seg_id: str, recorded: Sequence[float], emitted: Sequence[float],
             junction_refusals: Iterable[dict] = (),
             nodes: Sequence[Sequence[float]] | None = None) -> dict:
    """The verdict a caller records, with the refusals it does NOT waive.

    On a carriageway re-emit the junction refusals are reported as PRE-EXISTING
    and carried in the record verbatim.  They are not removed, not downgraded
    and not marked acceptable -- a later agent reading this record can refuse
    them, and anyone who moves one loses the certification by construction.

    CALL THIS AT THE POINT THE GATE READS IT, OVER THE EMITTED SET. NEVER
    COMPUTE IT ONCE AND CARRY A BOOLEAN.  `SeedField` named the erosion mode
    and it is the one that would undo this quietly: the moment the answer is a
    boolean held across a function boundary it is a `--re-emit` flag with an
    INTERNAL caller instead of an external one, and a later change that emits
    one moved station after the predicate was evaluated INHERITS the
    certification.  "Void on movement" is that stated as a LIFETIME rather than
    as a value -- a certification that nothing moved is only ever true of the
    emission it was computed from.  This module holds no state and caches
    nothing so that re-evaluating is always cheaper than storing.
    """
    cmp = compare(recorded, emitted, nodes)
    pre = list(junction_refusals)
    out = dict(cmp)
    out["segment"] = seg_id
    out["pre_existing_refusals"] = len(pre)
    out["pre_existing"] = pre
    if cmp["carriageway_unchanged"]:
        out["verdict"] = "RE-EMIT-CARRIAGEWAY"
        out["why"] = (
            f"all {cmp['stations']} CENTRELINE STATIONS are byte-identical to "
            f"the recorded profile: this write moves the CARRIAGEWAY by "
            f"0.000 m. It says NOTHING about the batter, which is measured "
            f"separately. The {len(pre)} junction refusal(s) are between "
            f"profiles ALREADY IN THE GROUND and are NOT waived: they remain "
            f"refused for any write that moves them.")
    else:
        out["verdict"] = "MOVES-CARRIAGEWAY"
        out["why"] = (
            f"{cmp['moved_count']} centreline station(s) differ from the "
            f"recorded profile, worst {cmp['worst_move_m']:.6f} m"
            + (" and the station count changed" if cmp["length_changed"] else "")
            + ". This write is judged on its own state, not as a re-emit.")
    return out


# ---------------------------------------------------------------------------
# SELFTEST -- BOTH DIRECTIONS, AND THE ACCEPTING ONE IS THE ONE THAT MATTERS.
#
# `SurveyNodes`' caution, which caught `north_segments.yaml` tonight: six
# refusing cases prove a guard FIRES; nothing proves it can PASS.  A guard
# never observed to say yes, met by the first honest re-emit, is the retired
# superset check again -- so the byte-identical case is asserted first.
# ---------------------------------------------------------------------------
def selftest(verbose: bool = True) -> dict:
    rec = [37.100, 36.981, 36.402, 35.877, 35.120]
    nodes = [[-292.0, 214.0], [-294.0, 214.0], [-296.0, 213.0],
             [-298.0, 212.0], [-300.0, 211.0]]
    out: dict[str, Any] = {}

    # (a) THE ACCEPTING DIRECTION.  A faithful re-emit must be observed to say
    #     yes, and must say so while still carrying its refusals.
    same = classify("T5-temple-dockshore", rec, list(rec),
                    junction_refusals=[{"pair": "T5/T13", "spread_m": 1.807}],
                    nodes=nodes)
    assert same["carriageway_unchanged"] is True, same
    assert same["verdict"] == "RE-EMIT-CARRIAGEWAY", same
    assert same["moved_count"] == 0 and same["worst_move_m"] == 0.0, same
    assert same["pre_existing_refusals"] == 1, \
        "a re-emit must CARRY its refusals, not drop them"
    out["reemit"] = same["verdict"]

    # (a2) THE RECORD MAY NOT OVERSTATE ITS SCOPE.  The verdict is about the
    #      CARRIAGEWAY; the batter is computed against terrain that may have
    #      moved since the record was written, so a record that reads "moved
    #      nothing" is false even when every station matches.  Asserted on the
    #      emitted text, because this is exactly the class of defect where the
    #      code is right and the sentence beside it is not -- BAND_M = 24.0
    #      over a population 54% beyond it, one artefact over.
    assert "is_reemit" not in same, \
        ("the key must be `carriageway_unchanged`; `is_reemit` reads as "
         "'nothing moved' and means 'no station moved'")
    assert same["batter_delta_m"] is None and same["batter_delta_note"], \
        "a null batter delta must say it was NOT LOOKED AT, never imply zero"
    assert "CARRIAGEWAY" in same["scope"] and "batter" in same["scope"], same
    low = same["why"].lower()
    assert "carriageway" in low and "nothing about the batter" in low, same["why"]
    for claim in ("moves ground by 0.000", "moved nothing", "nothing moved"):
        assert claim not in low, \
            f"the record claims {claim!r}, which is wider than what was measured"
    out["scope_stated"] = True

    # (b) ONE STATION MOVED BY THE STORAGE QUANTUM MUST REFUSE AND NAME IT.
    #     0.001 m is the smallest movement the plan file can express; a check
    #     that cannot see it is a band, and a band is not a pin.
    one = list(rec)
    one[3] = round(one[3] + QUANTUM_M, 6)
    moved = classify("T5-temple-dockshore", rec, one, nodes=nodes)
    assert moved["carriageway_unchanged"] is False, moved
    assert moved["verdict"] == "MOVES-CARRIAGEWAY", moved
    assert moved["moved_count"] == 1, moved
    assert moved["moved"][0]["station"] == 3, moved
    assert moved["moved"][0]["xz"] == [-298.0, 212.0], \
        "a refusal must NAME the station it refuses"
    assert abs(moved["worst_move_m"] - QUANTUM_M) < 1e-9, moved
    out["one_quantum"] = (moved["moved"][0]["station"],
                          moved["moved"][0]["xz"], moved["worst_move_m"])

    # (c) HALF A QUANTUM IS STILL MOVEMENT.  The direction `PIN_EXACT_M` got
    #     wrong: a band of half the quantum around a rounded value tolerated
    #     exactly the step it was meant to forbid.  There is no band here.
    half = list(rec)
    half[0] = rec[0] + QUANTUM_M / 2.0
    assert classify("x", rec, half)["carriageway_unchanged"] is False, \
        "half a quantum is movement; any tolerance here re-opens the pin defect"
    out["half_quantum_refused"] = True

    # (d) A STATION ADDED OR DROPPED IS MOVEMENT, not a pairwise comparison
    #     that runs off the end and reports the common prefix as unchanged.
    short = classify("x", rec, list(rec)[:-1])
    assert short["carriageway_unchanged"] is False and short["length_changed"], short
    longer = classify("x", rec, list(rec) + [34.9])
    assert longer["carriageway_unchanged"] is False and longer["length_changed"], longer
    out["length_change_refused"] = True

    # (e) THE CALLER CANNOT ASSERT IT.  There is no argument, kwarg or field by
    #     which a caller declares a re-emit: the only inputs are the record and
    #     the emission.  Checked structurally so the property cannot be lost by
    #     someone later adding a convenience parameter.
    import inspect                                          # noqa: PLC0415
    params = set(inspect.signature(classify).parameters)
    assert not (params & {"is_reemit", "reemit", "force", "waive", "accept"}), \
        ("classify() must DERIVE the verdict; a caller-asserted flag is "
         "CLEARABLE becoming an authorisation")
    out["derived_not_declared"] = True

    # (f) THE CERTIFICATION CANNOT BE INHERITED.  `SeedField`'s erosion mode:
    #     compute it once at the top of a write, carry a boolean, and a later
    #     change that emits one moved station AFTER the predicate was evaluated
    #     inherits a certification it never earned -- a `--re-emit` flag with an
    #     internal caller instead of an external one.
    #
    #     Two properties make that impossible and both are asserted, because
    #     the module being stateless today is not the same as it being unable
    #     to acquire state tomorrow.
    clean = classify("x", rec, list(rec))
    dirty = classify("x", rec, [rec[0] + QUANTUM_M] + list(rec[1:]))
    again = classify("x", rec, list(rec))
    assert clean["carriageway_unchanged"] is True, clean
    assert dirty["carriageway_unchanged"] is False, \
        "a second call must answer for ITS OWN emission, not the previous one"
    assert again["carriageway_unchanged"] is True, \
        "and the answer must not be poisoned by the call before it"
    module_state = [k for k, v in list(globals().items())
                    if isinstance(v, (dict, list, set))
                    and not k.startswith("__")
                    and k not in ("Moved",)]
    assert not module_state, \
        (f"module-level mutable state {module_state} is where a cached verdict "
         f"would live; this predicate must be recomputed at the point the gate "
         f"reads it, over the emitted set")
    out["not_inheritable"] = True

    # (g) EVERY VERDICT MUST NAME WHAT IT COMPARED AGAINST.  A verdict that
    #     does not say its reference is the same overstatement as a null that
    #     does not say which kind of null it is -- and it is the overstatement
    #     that voided a signed waiver on 2026-09-22: the attestation answered
    #     "did this campaign change the plan's heights" and was accepted for
    #     "does the record describe the ground".  Both true, different
    #     questions, and only one of them had been run.
    assert same["reference"] == PLAN_FILE, same
    assert "does NOT answer" in same["reference_note"], same["reference_note"]
    out["reference_named"] = True

    # (h) PLAN-TO-GROUND, on a CONSTRUCTED chain emission -- never the real
    #     ledger, which would make this case go quiet the day that file moves.
    seg = {"nodes": [[-292.0, 214.0], [-294.0, 214.0], [-296.0, 213.0]],
           "profile_y": [37.100, 36.981, 36.402]}
    agreed = compare_to_ground([[-292.0, 214.0, 37.100],
                                [-294.0, 214.0, 36.981],
                                [-296.0, 213.0, 36.402]], seg)
    assert agreed["agrees"] is True and agreed["moved_count"] == 0, agreed
    assert agreed["reference"] == LAST_EMISSION, agreed

    #     The T10/S8 shape: the ground holds a height the record does not.
    diverged = compare_to_ground([[-292.0, 214.0, 37.100],
                                  [-294.0, 214.0, 35.946],
                                  [-296.0, 213.0, 36.402]], seg)
    assert diverged["agrees"] is False and diverged["moved_count"] == 1, diverged
    assert abs(diverged["worst_move_m"] - 1.035) < 1e-6, diverged
    assert diverged["moved"][0]["xz"] == [-294.0, 214.0], diverged

    #     AND AN UNMATCHED STATION IS NOT AGREEMENT.  The emitter drops bridge
    #     stations, so a written profile legitimately holds fewer nodes than
    #     the record -- but a station this comparison could not speak for must
    #     never be counted as one it found equal.  That is absent-versus-empty
    #     inside the comparison itself.
    unmatched = compare_to_ground([[-999.0, 999.0, 1.0]], seg)
    assert unmatched["agrees"] is False, unmatched
    assert unmatched["unmatched_xz"] == 1 and unmatched["identical"] == 0, \
        "an unmatched station is 'could not speak for', never 'equal'"
    out["plan_to_ground"] = (diverged["worst_move_m"], unmatched["unmatched_xz"])

    # (i) AN ABSENT COMPARISON MUST NOT PRINT A MEASURED ZERO.  Three sightings
    #     tonight, in three different tools, all with the same signature:
    #       a ledger-vs-itself run printed `worst 0.000` on ALL 25 segments
    #       a run reading the ledger's [x, z, y] as [x, y, z] matched 0 of 244
    #         and printed "moved 0, worst 0.000000"
    #       a `NotMeasured` row read as agreement would print the same
    #     A ROW THAT MATCHED NOTHING AND A ROW THAT MATCHED EVERYTHING PRINT
    #     THE SAME TWO NUMBERS.  All three paths now refuse instead.
    nm = NotMeasured(measured=False, why="constructed absent row")
    assert not nm, "NotMeasured must be FALSY so `if not e:` catches it"
    for bad, what in ((nm, "a NotMeasured row"), ([], "an empty emission")):
        try:
            compare_to_ground(bad, seg)
        except SystemExit as exc:
            assert "NOT agreement" in str(exc), str(exc)
        else:
            raise AssertionError(
                f"compare_to_ground accepted {what} and would have returned "
                f"zero movement over an empty comparison")
    for rec_, emi_ in (([], [1.0]), ([1.0], [])):
        try:
            compare(rec_, emi_)
        except SystemExit as exc:
            assert "MUST REPORT ITS MATCH COUNT" in str(exc), str(exc)
        else:
            raise AssertionError(
                "compare() accepted an empty side and would have reported "
                "moved_count 0 over nothing")
    out["absent_is_not_zero"] = True

    # (j) AND THE DOCSTRING MAY NOT NAME LIVE DATA.  The claim that used to sit
    #     in `last_emission` -- naming two segments as having no profile in the
    #     chain -- was false, was retracted in chat, and STAYED IN THE CODE for
    #     another agent to find while grounding a console write. A constraint
    #     that lives in a chat log expires when the session does.
    doc = last_emission.__doc__ or ""
    import re as _re                                         # noqa: PLC0415
    # ALL THREE THINGS THE DOCSTRING CLAIMS IT EXCLUDES, because a check that
    # enforces less than its own prose claims is the defect this module is
    # about -- caught here by testing the docstring's claim against the check
    # and finding the check narrower.
    named = (_re.findall(r"\b[TSNW]\d+[a-z]?\b", doc)          # segment ids
             + _re.findall(r"\bseq\s+\d+", doc)                 # ledger seqs
             + _re.findall(r"\b\d+\.\d{3,}\b", doc))          # magnitudes
    # ABSOLUTE, not "after a retraction marker". The marker form was tried and
    # it is decorative: it permits each new stale example to be appended below
    # the previous one, which is how the list of struck claims becomes the
    # docstring. Live data does not belong here in any position.
    assert not named, \
        (f"last_emission's docstring names live data {sorted(set(named))}; a "
         f"docstring citing the chain is a transcription with no producer -- "
         f"it goes stale the moment the chain moves, and a stale docstring is "
         f"indistinguishable from one that was always wrong. ASK THE CHAIN.")
    out["docstring_names_no_live_data"] = True

    # (k) WHOSE DIVERGENCE IS IT.  The T12 shape, constructed: a segment that
    #     doubles back on itself, ONE reconciliation record with a 4 m taper,
    #     and divergence on BOTH sides of the fold.  `compare_to_ground` says
    #     "3 moved"; that one number is two populations and they need opposite
    #     handling -- one is the repair this write is carrying, the other is a
    #     plan the emitter rewrote and nobody wrote back.
    #
    #     THE ACCEPTING DIRECTION FIRST: a segment with NO divergence must be
    #     observed to answer zero and zero, or a classifier that never says
    #     "clean" is the retired-superset check again.
    fold = {"id": "F1-fixture",
            #    st0        st1        st2       st3        st4        st5
            "nodes": [[0.0, 0.0], [0.0, 2.0], [0.0, 4.0], [2.0, 4.0],
                      [2.0, 2.0], [2.0, 0.0]],
            "profile_y": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
            "reconciled": [{"at": [0.0, 4.0], "taper_m": 3.0}]}
    emitted = [[x, z, 10.0] for x, z in fold["nodes"]]
    clean = divergence(fold, emitted)
    assert clean["matched_xz"] == 6 and clean["unmatched_xz"] == 0, clean
    assert clean["deliberate_count"] == 0 and clean["drift_count"] == 0, clean
    out["divergence_clean"] = True

    #     NOW THE SPLIT.  st2 is the reconciliation's own anchor and a 3.0 m
    #     taper reaches st1..st3 ALONG the road.  st5 sits 4.0 m from the
    #     anchor in a straight line -- across the fold -- and 10.0 m along the
    #     carriageway.  That is the difference between "this write is carrying
    #     that repair" and "this write is pushing a stale height into the
    #     world", and it is decided by which distance you measure.
    ground_nodes = [[0.0, 0.0, 10.0], [0.0, 2.0, 10.0], [0.0, 4.0, 8.0],
                    [2.0, 4.0, 10.0], [2.0, 2.0, 10.0], [2.0, 0.0, 7.5]]
    d = divergence(fold, ground_nodes)
    assert d["matched_xz"] == 6 and d["unmatched_xz"] == 0, d
    assert [r["station"] for r in d["deliberate"]] == [2], d["deliberate"]
    assert [r["station"] for r in d["drift"]] == [5], d["drift"]
    assert abs(d["worst_deliberate_m"] - 2.0) < 1e-9, d
    assert abs(d["worst_drift_m"] - 2.5) < 1e-9, d
    assert d["drift_xz"] == [[2.0, 0.0]], \
        "a drifted station must be NAMED: a gate cannot act on a count"
    out["divergence_split"] = (d["deliberate_count"], d["drift_count"])

    #     AND THE EUCLIDEAN VERSION IS WRONG ON THIS FIXTURE, asserted rather
    #     than believed.  st4 at (2, 2) is 2.828 m from the anchor at (0, 4) --
    #     INSIDE a 3.0 m radius -- and 6.0 m along the carriageway, which is
    #     DOUBLE the taper.  A radius window launders that station's emit-time
    #     drift into a reconciliation this segment never made; the arc-length
    #     window this classifier uses calls it drift and names it.
    import math as _math                                      # noqa: PLC0415
    assert _math.hypot(2.0 - 0.0, 2.0 - 4.0) < 3.0, \
        "fixture is wrong: st4 must be inside the euclidean radius"
    d4 = divergence(fold, [[0.0, 0.0, 10.0], [0.0, 2.0, 10.0],
                           [0.0, 4.0, 10.0], [2.0, 4.0, 10.0],
                           [2.0, 2.0, 9.0], [2.0, 0.0, 10.0]])
    assert [r["station"] for r in d4["drift"]] == [4], \
        ("st4 is 2.828 m from the anchor but 6.0 m along the carriageway, "
         "past a 3 m taper: a euclidean window would launder emit-time drift "
         "into a reconciliation this segment never made")
    assert d4["deliberate_count"] == 0, d4
    out["divergence_is_arc_length"] = True

    #     AND AN ABSENT EMISSION IS REFUSED HERE TOO, for the same reason
    #     `compare_to_ground` refuses it: "0 deliberate / 0 drift" over a row
    #     nothing read is byte-identical to a measured clean segment.
    for bad in (NotMeasured(measured=False, why="constructed absent row"), []):
        try:
            divergence(fold, bad)
        except SystemExit as exc:
            assert "NOT agreement" in str(exc), str(exc)
        else:
            raise AssertionError(
                "divergence() accepted an absent emission and would have "
                "reported a clean split over nothing")
    out["divergence_absent_refused"] = True

    if verbose:
        for k, v in out.items():
            print(f"  {k}: {v}")
        print("reemit selftest OK")
    return out


def restore(seg: dict, emission_nodes) -> dict:
    """PUT THE GROUND BACK INTO THE PLAN, at the drifted stations only.

    The repair for what `divergence` finds.  Every EMIT-TIME DRIFT station
    takes the height from this segment's own last emission -- VERBATIM, never
    rounded, because a 3-decimal round of a value the ledger holds at full
    precision leaves a residue this module would then correctly report as
    movement all over again.

    THE DELIBERATE STATIONS ARE NOT TOUCHED.  They are the repair a campaign
    is carrying; overwriting them with the ground would silently discard a
    reconciliation somebody costed.  A tool that "restores the plan" and
    quietly undoes the plan is the worst possible shape for this.

    THE GRADE FIELDS ARE RE-MEASURED, not inherited.  `grade_max_measured` is
    a measurement OF THE PROFILE; carrying the old number beside a changed
    profile is the docstring-citing-live-data defect with a float instead of
    a sentence.  MEASURED over the segment's own node spacing, which is what
    `network.py` and `deviate.py` both do -- and which is a DIFFERENT and
    coarser quantity than the 1 m walked gradient, exactly as
    `junction.py::declared_vs_walked` says.

    Returns the mutated record and the row-by-row account.  The caller writes
    the file: this function does no I/O, so a dry run and a real one measure
    the same thing.
    """
    import math                                             # noqa: PLC0415

    div = divergence(seg, emission_nodes)
    ground = {(round(float(x), 2), round(float(z), 2)): float(y)
              for x, z, y in emission_nodes}
    nodes = [(round(float(a), 2), round(float(b), 2)) for a, b in seg["nodes"]]
    prof = [float(v) for v in seg["profile_y"]]
    changed = []
    drifted = {(round(p[0], 2), round(p[1], 2)) for p in div["drift_xz"]}
    for i, k in enumerate(nodes):
        if k not in drifted:
            continue
        was, now = prof[i], ground[k]
        prof[i] = now
        changed.append({"station": i, "xz": [k[0], k[1]],
                        "was": was, "now": now,
                        "delta_m": round(now - was, 6)})
    grades = []
    for j in range(len(prof) - 1):
        run = math.hypot(nodes[j + 1][0] - nodes[j][0],
                         nodes[j + 1][1] - nodes[j][1])
        grades.append(abs(prof[j + 1] - prof[j]) / (run or float(
            seg.get("station_m") or 1.0)))
    seg["profile_y"] = prof
    was_max = seg.get("grade_max_measured")
    if grades:
        seg["grade_max_measured"] = round(max(grades), 4)
        seg["grade_mean"] = round(sum(grades) / len(grades), 4)
        seg["grade_p95"] = round(sorted(grades)[int(0.95 * len(grades))], 4)
    return {
        "segment": seg.get("id"),
        "divergence": div,
        "restored_count": len(changed),
        "restored": changed[:40],
        "worst_restored_m": (max(abs(c["delta_m"]) for c in changed)
                             if changed else 0.0),
        "deliberate_left_alone": div["deliberate_count"],
        "grade_max_measured_was": was_max,
        "grade_max_measured_now": seg.get("grade_max_measured"),
        "tool": "roads/reemit.py::restore",
    }


def main(argv=None) -> int:
    """`report` every built segment's divergence; `restore` one segment's.

    THE TOOL THE REFUSAL NAMES.  `ribbon.py` refuses a write whose junction
    rows stand on stations the plan holds and the world does not, and tells
    the operator to restore the plan to the ground.  A refusal that names an
    operation nobody implemented is a refusal with no exit, so the operation
    lives here, beside the classifier that finds the condition.
    """
    import argparse                                         # noqa: PLC0415
    import json as _json                                    # noqa: PLC0415
    import sys                                              # noqa: PLC0415
    from pathlib import Path                                # noqa: PLC0415

    import yaml                                             # noqa: PLC0415

    here = Path(__file__).resolve().parent
    if str(here.parent) not in sys.path:
        sys.path.append(str(here.parent))
    import worldseed                                        # noqa: PLC0415
    ap = argparse.ArgumentParser(description=main.__doc__.split("\n")[0])
    ap.add_argument("op", choices=["report", "restore", "selftest"],
                    nargs="?", default="selftest")
    ap.add_argument("--segment", action="append", default=[])
    ap.add_argument("--ledger", default=None,
                    help="the chain (default: this run's world's, "
                         "worldseed.ledger_path())")
    ap.add_argument("--apply", action="store_true",
                    help="WRITE the restored profile back into the plan file "
                         "the segment came from. Without it nothing is "
                         "written and the same numbers are measured.")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    if a.op == "selftest":
        selftest()
        return 0

    a.ledger = a.ledger or str(worldseed.ledger_path())
    files = [p for p in worldseed.segment_files() if p.exists()]
    docs = {p: yaml.safe_load(p.read_text()) for p in files}
    where = {s["id"]: p for p, d in docs.items() for s in d["segments"]}
    out = {}
    for p, d in docs.items():
        for s in d["segments"]:
            if a.segment and s["id"] not in a.segment:
                continue
            em = last_emission(a.ledger,
                               f"road_{s['id']}".replace("-", "_"))
            if not em:
                # ABSENT, AND IT SAYS SO. An unbuilt segment has no ground to
                # be restored to; that is not agreement and not a clean row.
                out[s["id"]] = {"measured": False, "why": em.get("why")}
                continue
            out[s["id"]] = (restore(s, em["nodes"]) if a.op == "restore"
                            else {"divergence": divergence(s, em["nodes"])})
    for sid, r in sorted(out.items()):
        if r.get("measured") is False:
            print(f"{sid:30s} NOT MEASURED -- no emission in the chain")
            continue
        dv = r["divergence"]
        line = (f"{sid:30s} {dv['matched_xz']:4d}/{dv['stations']:<4d} matched"
                f"  deliberate {dv['deliberate_count']:3d} "
                f"(worst {dv['worst_deliberate_m']:6.3f})"
                f"  DRIFT {dv['drift_count']:3d} "
                f"(worst {dv['worst_drift_m']:6.3f})")
        if a.op == "restore":
            line += (f"  -> restored {r['restored_count']:3d}, grade_max "
                     f"{r['grade_max_measured_was']} -> "
                     f"{r['grade_max_measured_now']}")
        print(line)
    if a.op == "restore" and a.apply:
        touched = {where[sid] for sid in out if out[sid].get("measured")
                   is not False and out[sid]["restored_count"]}
        for p in sorted(touched):
            p.write_text(yaml.safe_dump(docs[p], sort_keys=True,
                                        default_flow_style=False))
            print(f"wrote {p}")
        if not touched:
            print("nothing to write: no drifted station in the selection")
    elif a.op == "restore":
        print("DRY RUN -- pass --apply to write the plan file")
    if a.json:
        Path(a.json).write_text(_json.dumps(out, indent=1, default=str))
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
