#!/usr/bin/env python3
"""THE SECTION REGISTRY - and the two refusals that are the point of it.

Every defect a human found in this world had already been declared correct by
a check.  Not one of those checks could have failed: `WALKABLE, 0 holes,
0 gaps`, `349/349 at tolerance 0`, `184 objects left standing`, `portal hall
-> Vestvik 3,248.7 m`.  They were written, they were run, they returned green,
and a human walked into a 6.12 m wall.

A gate that lets such a check be added is not a gate.  So this file refuses:

  REFUSAL 1 - A SECTION WITH NO FAILING SELFTEST CASE CANNOT BE REGISTERED.
      `register()` raises at IMPORT time, so the gate does not start at all.
      Every one of the four checks above would have been rejected by it:
      none of them had a state they were shown to refuse.  A check proved
      only to pass is a check that has never been proved to be a check.

  REFUSAL 2 - A SECTION WITHOUT ITS POPULATION CANNOT BE REGISTERED.
      "184 objects left standing, each with its blocker named" was a TRUE
      count of CLEARABLE VEGETATION offered in answer to a question about
      STRUCTURES.  A count without its population is how that happens, so
      `measures`, `instrument` and `population` are required fields and a
      failing Finding must carry either a coordinate or a number.

Adding a section is: write `run(ctx) -> list[Finding]`, write CASES with at
least one `expect_fail=True` that encodes a state this world is actually
known to have been in, and call `register()`.  Nothing else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,23}$")

# Placeholder prose is the same failure as no prose: it names nothing.  A
# section whose population is "various" cannot be audited months from now,
# which is the moment this whole file exists for.
_EMPTY_WORDS = {"", "-", "n/a", "na", "tbd", "todo", "various", "stuff",
                "things", "objects", "everything", "world", "unknown", "?"}


# ---------------------------------------------------------------------------
# what a section reports
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    """One measured statement about the world.

    `ok=False` is a REFUSAL and must carry evidence: a coordinate, a measured
    number, or both.  `Finding(ok=False, message="looks wrong")` is rejected
    by the runner, because a refusal a builder cannot act on is noise and a
    refusal without a number is an opinion.
    """

    check: str                     # which check inside the section
    ok: bool
    message: str                   # one line, with its numbers IN it
    where: tuple | None = None     # (x, z) or (x, y, z), world coordinates
    measured: float | None = None
    limit: float | None = None
    numbers: dict[str, Any] = field(default_factory=dict)
    # A section-wide population is declared once on the Section; this field is
    # for a finding whose population is NARROWER than the section's.
    population: str = ""
    # Reported, named, and does NOT decide the exit code.  For a reading whose
    # precision is known to be worse than the thing being judged - a
    # mesh-collider boulder recorded only as its bounding box - where failing
    # the world would be a number without its precision.
    advisory: bool = False
    section: str = ""              # filled in by the runner

    def evidence(self) -> bool:
        return (self.where is not None or self.measured is not None
                or bool(self.numbers))


@dataclass
class SectionResult:
    """What a section hands back.

    A section MAY simply return `list[Finding]` from `run()`; the runner wraps
    it.  A section that wants its own report block and its own printed form -
    ROADS does, because its output predates the registry and must not move -
    returns one of these instead.
    """

    findings: list[Finding] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    render: Callable[[Callable[[str], Any]], None] | None = None
    # A section may override the pass/fail it derives from its findings only
    # to be STRICTER, never laxer; the runner enforces that.
    passed: bool | None = None

    def verdict(self) -> bool:
        derived = not any((not f.ok) and not f.advisory for f in self.findings)
        if self.passed is None:
            return derived
        return bool(self.passed) and derived if derived else False


@dataclass
class Case:
    """One selftest case: a STATE, a CHECK, and which way it must come out.

    `run()` returns the findings the check produces for a state built here in
    code - no world, no save, no server, so the proof travels with the gate
    and a future world inherits it.

    `expect_fail=True`  the check MUST refuse this state.
    `expect_fail=False` the check MUST accept it.

    Both directions are required because a check that refuses everything is as
    useless as one that refuses nothing, and because several of the cases
    below are the OLD blind check being shown to accept the known-bad state -
    that is the record of why the defect shipped, and it belongs in the gate.
    """

    name: str
    expect_fail: bool
    run: Callable[[], Iterable[Finding] | Finding | bool]
    # Optional: which state this encodes, for the report.  A case that names a
    # real prior state of this world is worth more than an invented one.
    prior_state: str = ""
    # OPTIONAL BUT STRONGLY WANTED: a zero-argument callable that RESTORES
    # THE DEFECT and returns a zero-argument undo.  See `run_cases`.
    #
    # WHY THIS FIELD EXISTS.  Rule 4 says a check must have a state it is
    # REQUIRED to refuse.  Nothing said that state must STILL BE that state,
    # and on 2026-09-21 three cases in two sections had quietly stopped
    # describing anything:
    #
    #   structures `deck_open_to_the_sky_through_a_hole_in_the_roof` was a
    #     6 x 2 m gable whose gap spanned the roof's whole DEPTH, so it
    #     modelled a ridge SLOT and not an enclosed hole - and it was about
    #     to veto the only one of four candidate discriminators that
    #     separates this world.
    #   harbours `refine_45_deg...` was AXIS-ALIGNED, where 19 offsets
    #     resolve to 19 vertices, so it was structurally incapable of seeing
    #     the 45-degree collapse that made a 13-vertex channel read 19 m
    #     wide.
    #   harbours `entry_head_room_never_measured_for_this_hull` asserted
    #     `CargoShip.air_m is None`, true only while the piece corpus was
    #     vanilla-only; the merged corpus measured CargoShip at 9.230 m and
    #     the case's own PREMISE evaporated.
    #
    # All three were found by accident.  THE RULE IS THE GATE'S FIRST RULE
    # ONE LEVEL UP - "the reference must not come from the artefact under
    # test", and the missing corollary is that THE FIXTURE MUST NOT EITHER: a
    # fixture must CONSTRUCT the state it tests and never BORROW it, because
    # the moment it leans on a corpus, a save, a placement or a real prefab
    # it inherits that artefact's mutability.  And it then goes QUIET rather
    # than red, which is the dangerous direction: a failing case is fixed in
    # an hour, a case that has stopped describing anything is a check nobody
    # knows they lost.
    #
    # A `neuter` is how a case proves it is still load-bearing without a
    # human re-deriving it.  Three agents did exactly this by hand in one
    # evening; it belongs in the harness.
    #
    # AND A NEUTER IS NOT SCORED ON THE VERDICT, BECAUSE THE VERDICT IS THE
    # WEAKEST THING A CHECK PRODUCES.  MEASURED 2026-09-22 on `lh-west`'s
    # pre-repair body, save c80ba7b465ab, by substituting the axis-aligned
    # bounding box for `structures.Columns.span` - the exact defect that
    # function's own docstring names, "a box cannot tell a gable from an X":
    #
    #   sighted instrument   inside_and_open  8   open 45/169   roofed 106
    #   BLIND instrument     inside_and_open 15   open 30/169   roofed 136
    #   both                 REFUSE
    #
    # A BLIND INSTRUMENT AND A SIGHTED ONE RETURN THE SAME VERDICT AND
    # DIFFERENT POPULATIONS, SO VERDICT-EQUALITY CERTIFIES A BLIND
    # INSTRUMENT.  COROLLARY: A CHECK MUST BE PROVED TO REFUSE, AND THE
    # REFUSAL MUST CARRY ITS POPULATION.  That is why this field exists and
    # why the unproven count is worth driving down while the case count is
    # not worth growing.
    #
    # THE SECOND HALF OF THE SAME LESSON: THE ACCEPTING ARM AND THE REFUSING
    # ARM NEED DIFFERENT NEUTERS.  `structures._neuter_roof_enclosure` widens
    # `roof_enclosed` to always-True and protects the arm that must ACCEPT a
    # ridge slot; the arm that must REFUSE `lh-west`'s oculus survives it
    # untouched, because widening a predicate can only ever add holes.  One
    # neuter per DIRECTION, not one per predicate.
    neuter: Callable[[], Callable[[], None]] | None = None
    # What the neuter puts back, for the report.
    neuters: str = ""
    # HOW the neuter proved this case, filled in by `run_cases`.  NOT an
    # input: an author cannot declare it, only earn it.
    #
    # "verdict"  the neutered run came out the OTHER WAY.  The check itself
    #            saw the restored defect and said so.
    # "assert"   the neutered run raised the case's OWN AssertionError.
    #            That is a real proof of a fixture invariant AND IT IS THE
    #            WEAK FORM, because A NEUTER THAT EMPTIES THE INSTRUMENT
    #            RAISES THE SAME EXCEPTION.  MEASURED 2026-09-23 on this
    #            file's own machinery: two provenance cases shared one
    #            neuter that switched the ground-key classifier off
    #            entirely; 0 of 4 dependencies survived it, the key became a
    #            CONSTANT, and both cases were stamped PROVEN LOAD-BEARING
    #            by an index that named nothing.  The first repair expressed
    #            the survivor as an `assert` - and this function reads an
    #            AssertionError as a flip, so the degenerate neuter STILL
    #            stamped PROVEN, now with a well-worded message.  Only when
    #            the survivor became a VERDICT did the harness report the
    #            truth.
    #
    # So the two are counted separately.  A high "assert" count is not
    # wrong; it is the number of proofs this harness CANNOT TELL APART from
    # an emptied instrument, and it belongs beside the unproven count for
    # the same reason that one does.
    proof_mode: str = ""


@dataclass
class Section:
    name: str
    measures: str        # what property of the world is asserted
    instrument: str      # which instrument produces the numbers
    population: str      # the exact set counted
    run: Callable[[Any], Any]
    cases: list[Case]
    # A whole section may be advisory if its precision is known to be worse
    # than the judgement - it still runs, still prints, never decides.
    advisory: bool = False

    def failing_cases(self) -> list[Case]:
        return [c for c in self.cases if c.expect_fail]


SECTIONS: dict[str, Section] = {}


def register(section: Section) -> Section:
    """Add a section, or REFUSE and stop the gate from starting."""
    n = section.name
    if not _NAME_RE.match(n or ""):
        raise ValueError(
            f"section name {n!r} must be lower-case, 3-24 chars, "
            "[a-z][a-z0-9_]*: it is used as a --section argument and as a "
            "JSON key")
    if n in SECTIONS:
        raise ValueError(
            f"section {n!r} is already registered by {SECTIONS[n].run}; two "
            "authorities for one question is how this project got two "
            "implementations of the same helper")
    if not callable(section.run):
        raise TypeError(f"section {n!r} has no run(ctx)")
    for fld in ("measures", "instrument", "population"):
        v = (getattr(section, fld) or "").strip()
        if v.lower() in _EMPTY_WORDS or len(v) < 12:
            raise ValueError(
                f"section {n!r} must declare {fld!r} in words that name the "
                f"exact thing (got {v!r}).  'population' is not optional: "
                "\"184 objects left standing\" was a true count of vegetation "
                "answering a question about structures.")
    if not section.cases:
        raise ValueError(f"section {n!r} registers no selftest cases")
    bad = [c.name for c in section.cases if not callable(c.run)]
    if bad:
        raise TypeError(f"section {n!r} cases are not callable: {bad}")
    if not section.failing_cases():
        raise ValueError(
            f"section {n!r} has no selftest case it is REQUIRED TO FAIL.  "
            "A check proved only to pass has never been proved to be a "
            "check; every defect a human found in this world had already "
            "been declared correct by exactly such a check.  Add a case "
            "with expect_fail=True encoding a state the world is known to "
            "have been in.")
    if not any(c for c in section.cases if not c.expect_fail):
        raise ValueError(
            f"section {n!r} has no case it must ACCEPT.  A check that "
            "refuses everything blocks a world that is fine and teaches the "
            "next builder to bypass the gate.")
    SECTIONS[n] = section
    return section


def normalise(section: Section, out) -> SectionResult:
    """Accept either a bare list of Findings or a SectionResult."""
    if isinstance(out, SectionResult):
        res = out
    elif isinstance(out, Finding):
        res = SectionResult(findings=[out])
    elif out is None:
        res = SectionResult()
    else:
        res = SectionResult(findings=list(out))
    for f in res.findings:
        if not f.section:
            f.section = section.name
        if section.advisory:
            f.advisory = True
        if not f.ok and not f.advisory and not f.evidence():
            raise ValueError(
                f"section {section.name!r} refused with no evidence: "
                f"{f.check}: {f.message!r}.  A refusal must carry a "
                "coordinate or a number - a builder cannot act on a verdict.")
    return res


def run_cases(section: Section) -> list[tuple[Case, bool, str]]:
    """Every case, run, and whether it came out the way it must.

    A case that raises is a FAILED case, not a skipped one: an instrument that
    cannot run on a fixture it was written for is exactly the state where a
    gate quietly stops gating.
    """
    out: list[tuple[Case, bool, str]] = []
    for c in section.cases:
        # Earned, never declared, and cleared first so a stale mode from an
        # earlier pass cannot be read as this pass's evidence.
        c.proof_mode = ""
        try:
            refused, detail = _verdict(c)
            ok = (refused == c.expect_fail)
            want = "REFUSE" if c.expect_fail else "ACCEPT"
            if not ok:
                out.append((c, False, f"the check had to {want} this state; "
                                      f"{detail}"))
                continue
            # AND NOW: IS THE CASE STILL LOAD-BEARING?
            #
            # With the defect restored verbatim, THIS case must come out the
            # OTHER way.  A must-refuse case that still refuses under the
            # neutered check is not testing the fix; a must-accept case that
            # still accepts is a decoration - and an accept case that cannot
            # fail is the same defect as a refuse case that cannot fire,
            # which almost nobody checks.
            if c.neuter is None:
                c.proof_mode = "unproven"
                out.append((c, True, ""))
                continue
            undo = c.neuter()
            # A CASE THAT ASSERTS ITS OWN FIXTURE INVARIANTS EXPRESSES "THE
            # OTHER WAY" AS A RAISE, NOT AS A FLIPPED VERDICT, and the first
            # version of this pass could not tell that from a broken case.
            # MEASURED within twenty minutes of the field existing:
            # `a_crown_out_of_reach_is_not_a_destination` neutered by
            # restoring the reach filter hands back `portal_wood`'s crown at
            # 36.843 m and the case's own AssertionError fires - which IS the
            # proof - and the pass reported "case raised" and reddened the
            # gate.  ALL SEVEN of that section's neuters prove themselves
            # this way, and several of its cases return `[]` in BOTH
            # directions by construction because they assert on resolver
            # OUTPUT rather than on findings, so a verdict comparison could
            # never prove them however the neuter is written.
            #
            # So a raise HERE is the other way.  A raise on the FIRST run
            # still fails the case, exactly as before - that distinction is
            # the whole point: the case must work on the fixed code and
            # break on the defective code, and only the second is allowed to
            # be an exception.
            # AND ONLY AN AssertionError IS A PROOF.  A neuter that patches
            # the wrong attribute, or leaves a None where a Solid was, raises
            # TypeError / AttributeError / KeyError from inside the code
            # under test - and a pass that counted any raise as "the other
            # way" would stamp that case PROVEN LOAD-BEARING having proved
            # nothing.  An enforcement that claims a check is held in place
            # when it is not is strictly worse than no enforcement, so the
            # five outcomes are separated and the exception TYPE is printed,
            # which is what makes a broken neuter distinguishable from a
            # proof at a glance:
            #
            #   neuter run raises AssertionError  the case's own assertion
            #                                     fired -> PROVEN
            #   neuter run raises anything else   THE NEUTER IS BROKEN -> fail
            #   neuter run flips the verdict      -> PROVEN
            #   neuter run repeats the verdict    -> NOT LOAD-BEARING
            #   FIRST run raises anything         broken case, unchanged
            broken = None
            try:
                nref, ndet = _verdict(c)
                flipped = nref != refused
                came = "REFUSE" if nref else "ACCEPT"
            except AssertionError as exc:
                flipped, came = True, "raised AssertionError"
                ndet = str(exc)
            except Exception as exc:                    # noqa: BLE001
                flipped, came = False, "raised"
                broken = type(exc).__name__
                ndet = f"{broken}: {exc}"
            finally:
                undo()
            if broken is not None:
                out.append((c, False, (
                    f"THE NEUTER IS BROKEN, not the case: restoring the "
                    f"defect ({c.neuters or 'see its neuter'}) raised "
                    f"{ndet}.  A neuter must restore the defect and let the "
                    f"CASE'S OWN assertion fire; any other exception comes "
                    f"from inside the code under test and proves nothing")))
            elif not flipped:
                out.append((c, False, (
                    f"THIS CASE IS NOT LOAD-BEARING: with the defect "
                    f"restored ({c.neuters or 'see its neuter'}) it still "
                    f"came out {came}, so it is not the case that holds the "
                    f"fix in place; {ndet}")))
            else:
                # THE DISTINCTION THIS FIELD EXISTS FOR.  A flipped VERDICT
                # is the check itself seeing the restored defect.  A raised
                # AssertionError is the case's own fixture invariant firing
                # - a real proof, and the weak form, because A NEUTER THAT
                # EMPTIED THE INSTRUMENT RAISES EXACTLY THE SAME EXCEPTION.
                # Counted apart so the weak form is a number that can be
                # driven down instead of a thing the next author has to
                # remember.
                c.proof_mode = ("assert" if came == "raised AssertionError"
                                else "verdict")
                out.append((c, True, ""))
        except Exception as exc:                        # noqa: BLE001
            out.append((c, False, f"case raised {type(exc).__name__}: {exc}"))
    return out


def _verdict(c: Case) -> tuple[bool, str]:
    """`(did it refuse, why)` for one case, run once."""
    got = c.run()
    if isinstance(got, bool):
        return got, f"refused={got}"
    fs = ([got] if isinstance(got, Finding)
          else ([] if got is None else list(got)))
    hard = [f for f in fs if not f.ok and not f.advisory]
    return bool(hard), ("; ".join(f.message for f in hard[:2])
                        or f"{len(fs)} findings, none refusing")
