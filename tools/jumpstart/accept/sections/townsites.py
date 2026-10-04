"""TOWN SITES: a town stands where THIS seed's ground says, or the plan stops.

WHAT THIS SECTION EXISTS TO CATCH.  `settlements/plan.py::build` carried, until
2026-09-21, two coordinates written inline:

    want = {"stenvik": (496, 904), "vestvik": (-906, 1846)}
    ...
    raise ValueError(f"the recorded {spec['id']} centre {want[spec['id']]} is "
                     f"no longer a town candidate: the search moved, and a "
                     f"plan that silently relocates a town is worse than one "
                     f"that stops. Candidates: {sorted(by_xz)}")

THE REFUSAL IS CORRECT AND IS KEPT.  `sites.yaml` is the node set RoadNet and
Crossings build against and the town may already be standing, so a plan that
moves a town quietly moves a road network and abandons buildings; stopping is
strictly better.  What was wrong is WHAT IT COMPARED AGAINST.  Those two points
are Pirate68's land and nobody else's, so on every other seed the refusal was
GUARANTEED to fire -- before one site was planned -- and its message named two
coordinates from a world the operator is not building.  A guard that cannot
pass is a blocker wearing a guard's clothes, and an unactionable message is how
a stop becomes a dead end rather than a fix.  MEASURED consequence: no seed but
Pirate68 could produce a `sites.yaml` at all, and `sites.yaml` is what supplies
16 of the road network's nodes.

So the comparison is against a DECLARATION scoped to the seed it is a statement
about (`plan.ANCHORED_TOWNS`, or `--expect-towns id=x,z`), and a seed with no
declaration has nothing to be relocated FROM: its towns are the district
search's own top-ranked candidates on its own land.

POPULATION, for the cases: candidate sets CONSTRUCTED here, never a height
field, never `/tmp`, never `sites.yaml`.  `plan.select_towns` is pure in its
inputs precisely so this is possible -- a fixture that needed the 230 MB
Pirate68 field to test seed-independence would be testing it on Pirate68.

POPULATION, for `run`: the town records of the published `settlements/sites.yaml`
against the declaration in force for the seed THAT FILE says it was solved on.
That is the one thing the cases cannot see: a declaration edited without
re-publishing leaves a `sites.yaml` in the tree whose towns nothing declares.

NO WORLD, NO SAVE, NO SERVER.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .. import instruments as I
from .. import termination as T
from ..registry import Case, Finding, Section, register

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent.parent


# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# THE POPULATION IS `termination.emittable("townsites")`, and BOTH of its
# refusals are about a PUBLISHED ARTEFACT rather than about ground.
# Neither is contested, neither is obstructed, and neither has a terrain
# question to ask: the published site set and the declaration that governs
# it disagree, so one of them moved without the other, and the remedy in
# both shapes is TO RUN THE PRODUCER AGAIN for this seed.  That is a
# prerequisite this tree performs - `settlements/plan.py::build` - and it
# has not run, which is 1E and not 1C.
TERMINATION_FAMILIES = {
    "published_sites_are_the_declared_set": (
        "PRODUCER_NOT_RUN",
        "re-run settlements/plan.py::build for this seed so the published "
        "site set is the set this plan declares; RoadNet adopts every "
        "record in that file as a network node, so the artefact and the "
        "declaration must be one thing"),
    "corpus_is_about_this_world": (
        "PRODUCER_NOT_RUN",
        "re-run settlements/plan.py::build for this world and seed; a "
        "corpus declaring another world is a reading of a DIFFERENT WORLD, "
        "not a defect of this one"),
}


def _sites_path(seed: str):
    """WHERE THIS SEED'S SOLVED SITE SET IS, ASKED OF THE READER THAT OWNS
    THE QUESTION.

    `roads/spec.py::sites_path` answers `settlements/sites.yaml` for the
    default seed, `settlements/sites.<seed>.yaml` for any other, and
    `$VH_SITES` over both.  `accept/sections/provenance.py:174` already asks
    it; a second answer here would disagree with the planner on exactly the
    seeds where it matters, which is the fault this file exists to undo.
    """
    return I._worldseed().sites_path(seed)


_PLAN = None


def _plan():
    """`settlements/plan.py`, LOADED BY PATH, NOT BY NAME.

    There are three `plan.py` under tools/jumpstart -- settlements/,
    crossings/ and network/ -- and whichever importer gets there first owns
    `sys.modules["plan"]` for the whole run, so `import plan` resolves
    differently in a single-section selftest than in a full one.  Same trap
    `accept/sections/provenance.py` records against the same file.
    """
    global _PLAN
    if _PLAN is None:
        settlements = JUMPSTART / "settlements"
        if str(settlements) not in sys.path:
            sys.path.insert(0, str(settlements))
        spec = importlib.util.spec_from_file_location(
            "_townsites_settlements_plan", settlements / "plan.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PLAN = mod
    return _PLAN


# ---------------------------------------------------------------------------
# THE FIXTURE.  A town candidate is CONSTRUCTED, with numbers that belong to no
# world: every coordinate below is deliberately unlike Ulfsland's, so a check
# that has quietly gone back to matching Pirate68's pair cannot pass by
# coincidence.
# ---------------------------------------------------------------------------
def _candidate(x: float, z: float, patch: str = "mainland", *,
               relief: float = 9.4, meadows: float = 0.97,
               coast: float = 0.11, ground: float = 41.25,
               spawn_m: float = 880.0):
    S = _plan().S
    return S.Site(
        kind="town", x=x, z=z, patch=patch, ground_y=ground, biome="Meadows",
        pad={"x": x, "z": z, "width_m": 24.0, "depth_m": 24.0,
             "target_y": ground, "moved_m3": 0.0, "max_cut_m": 0.0,
             "max_fill_m": 0.0, "clamp_ok": True, "clamp_headroom_m": 8.0,
             "wet_samples": 0},
        rise_m=6.0, dist_water_m=120.0, dist_spawn_m=spawn_m,
        nearest_location={"prefab": "Runestone", "dist_m": 310.0},
        notes={"district_m": 176.0, "district_relief_m": relief,
               "district_meadows_fraction": meadows,
               "coast_fraction_400m": coast, "square_m": 24.0})


# Three candidates on a seed that is not this world's.  None of them is at
# either Ulfsland centre, which is the whole point.
def _fresh_candidates():
    return [_candidate(1180.0, -2044.0, "mainland"),
            _candidate(-311.0, 2570.0, "westisle", relief=7.2, meadows=1.0),
            _candidate(2004.0, 611.0, "mainland", relief=11.3, meadows=0.93)]


ULFSLAND_DIGITS = ("496", "904", "-906", "1846")


def _mentions_ulfsland(text: str) -> list[str]:
    return [d for d in ULFSLAND_DIGITS if d in text]


# ---------------------------------------------------------------------------
# run: the published site set against the declaration in force for its seed
#
# AND ONLY EVER FOR THE WORLD THIS RUN WAS ASKED ABOUT.  Until 2026-09-23
# this function opened `SITES_YAML` - a FIXED path in the repository - and
# handed the document to `audit`, which measures it against the declaration
# for THE SEED THAT FILE NAMES.  On `--world Fresh42` that read Ulfsland's
# published set, compared it with Pirate68's declaration, agreed with itself,
# AND REPORTED PASS about a world the run was not asked about.  A green
# verdict about the wrong world is strictly worse than a red one, because a
# red one gets looked at.
#
# The corpus is now read through `instruments.read_scoped_corpus`, the one
# gate-side guard, at the path `roads/spec.py::sites_path` resolves for THIS
# RUN'S SEED - so a world that has solved its own site set is audited against
# its own, and only a foreign, absent or header-less one is refused.  There
# is then no document here to report a verdict over.
# ---------------------------------------------------------------------------
def run(ctx) -> list[Finding]:
    want_seed, _want_world = I.run_scope(ctx)
    doc, refusals = I.read_scoped_corpus(
        ctx, "the published site set", resolve=_sites_path,
        produced_by="settlements/plan.py::build")
    # BOTH RETURNS ARE BOARD ROWS, and the first one's refusal is emitted
    # by a shared instrument rather than by this file.
    out = refusals or audit(doc or {}, str(_sites_path(want_seed)))
    return out + T.classify(sys.modules[__name__], out, "townsites")


def audit(doc: dict, where: str) -> list[Finding]:
    """The published site set against the declaration in force for ITS OWN
    declared seed.

    Takes the DOCUMENT, not a path, so the states it refuses can be
    CONSTRUCTED in a case instead of borrowed from whatever `sites.yaml`
    happens to be on the host -- which is the fixture rule, and this check is
    about a published artefact, so it is the one most at risk of testing the
    host instead of the code.
    """
    pm = _plan()
    out: list[Finding] = []
    seed = doc.get("seed")
    # EVERY PUBLISHED SITE, not just the towns.  This section's own run()
    # checked 2 of 16 records until 2026-09-22, which is the population error
    # it exists to catch, in the check itself: 14 outlying sites could have
    # been published at coordinates nothing declares and this would have
    # reported two green findings and stopped.
    declared_ids = {t["id"] for t in pm.TOWNS} | {o["id"] for o in pm.OUTLIERS}
    records = [r for r in (doc.get("sites") or [])
               if r.get("id") in declared_ids]
    if not seed:
        return [Finding(
            check="published_town_centres", ok=True, advisory=True,
            message=(f"{where} declares no seed, so its {len(records)} "
                     f"town centres cannot be checked against any "
                     f"declaration; the provenance section owns that refusal"),
            numbers={"towns": len(records)})]
    town_anchors, town_source = pm.declared_town_centres(str(seed))
    site_anchors, site_source = pm.declared_outlier_sites(str(seed))
    anchors = {**town_anchors, **site_anchors}
    source = town_source or site_source or "nothing"
    if not records:
        out.append(Finding(
            check="published_town_centres", ok=True, advisory=True,
            message=(f"{where} publishes no record for any of the "
                     f"{len(declared_ids)} sites this plan builds"),
            numbers={"seed": str(seed)}))
    # TWO DIFFERENT OBJECTS, AND ONLY ONE OF THEM IS THE DECLARATION.
    # A town's published `xz` IS its declared centre -- `layout.TownPlanner`
    # lays the streets out around the point it was given.  An outlier's is
    # NOT: `build` feeds the declared centre to `sites.best_pad`, which slides
    # the pad inside its own search box to clear the POI stand-off, and
    # publishes WHERE THE PAD LANDED.  MEASURED on the published Ulfsland set:
    # 11 of 14 outliers differ from their declaration, tree-west by
    # (+16, -6) m -- the slide, not a drift.
    #
    # So the hard assertion here is MEMBERSHIP, which is exact and needs no
    # tolerance: every published site is one this plan builds, and when a
    # declaration exists for the seed, every declared site is published.  The
    # offset is REPORTED as a number and never judged, because judging it
    # would need `best_pad`'s search box -- and a tolerance standing in for a
    # population is the move this section exists to undo.
    published = {r["id"] for r in records}
    extra = sorted(r.get("id") for r in (doc.get("sites") or [])
                   if r.get("id") not in declared_ids)
    if extra:
        out.append(Finding(
            check="published_sites_are_the_declared_set", ok=False,
            message=(f"{where} publishes {extra} for seed {seed!r}, which "
                     f"this plan does not build; RoadNet adopts every record "
                     f"in this file as a network node"),
            numbers={"seed": str(seed), "undeclared": extra}))
    if anchors:
        missing = sorted(k for k in anchors if k not in published)
        if missing:
            out.append(Finding(
                check="published_sites_are_the_declared_set", ok=False,
                message=(f"{source} declares {missing} for seed {seed!r} and "
                         f"{where} publishes no record for them: the "
                         f"declaration and the artefact RoadNet reads "
                         f"disagree, so one of them moved without the other"),
                numbers={"seed": str(seed), "declared_not_published": missing}))
    for r in records:
        got = (int(round(float(r["xz"][0]))), int(round(float(r["xz"][1]))))
        a = anchors.get(r["id"])
        if a is None:
            out.append(Finding(
                check="published_sites_are_the_declared_set", ok=True,
                advisory=True,
                message=(f"{r['id']} is published at {got} for seed {seed!r} "
                         f"and NOTHING DECLARES it, so a re-solve may move it "
                         f"and no check would refuse; declare it with "
                         f"--expect-sites {r['id']}={got[0]},{got[1]}"),
                numbers={"seed": str(seed), "xz": list(got)}))
            continue
        want = (int(round(float(a["xz"][0]))), int(round(float(a["xz"][1]))))
        out.append(Finding(
            check="published_sites_are_the_declared_set", ok=True,
            advisory=got != want,
            message=(f"{r['id']} is published at {got}, declared at {want} by "
                     f"{source} for seed {seed!r}"
                     + ("" if got == want else
                        f" -- offset ({got[0] - want[0]}, {got[1] - want[1]}) m, "
                        f"the pad slide best_pad applied to clear the POI "
                        f"stand-off; NOT judged here")),
            numbers={"seed": str(seed), "published": list(got),
                     "declared": list(want),
                     "offset_m": [got[0] - want[0], got[1] - want[1]]}))
    return out


def _published(seed: str, ids, *, extra=None) -> dict:
    """A CONSTRUCTED sites.yaml document: the declared coordinates for `ids`
    on `seed`, plus any `extra` records nothing declares."""
    pm = _plan()
    anchors = {**pm.declared_town_centres(seed)[0],
               **pm.declared_outlier_sites(seed)[0]}
    sites = [{"id": i, "xz": [float(anchors[i]["xz"][0]),
                              float(anchors[i]["xz"][1])]} for i in ids]
    for e in (extra or ()):
        sites.append({"id": e, "xz": [0.0, 0.0]})
    return {"seed": seed, "world": None, "sites": sites}


def _case_a_published_site_nothing_builds_is_refused():
    """RoadNet ADOPTS EVERY RECORD IN THIS FILE as a network node, so a record
    for a site this plan does not build is a node with no owner."""
    pm = _plan()
    ids = [t["id"] for t in pm.TOWNS]
    doc = _published("Pirate68", ids, extra=["u-somebody-elses-keep"])
    fs = audit(doc, "<constructed>")
    hard = [f for f in fs if not f.ok and not f.advisory]
    if hard:
        msg = "; ".join(f.message for f in hard)
        assert "u-somebody-elses-keep" in msg, \
            f"the refusal must name the undeclared record: {msg[:200]}"
        return Finding(
            check="published_site_nothing_builds_refused", ok=False,
            message=(f"a published set carrying a record no plan builds was "
                     f"refused: {msg[:120]}"),
            numbers={"undeclared": "u-somebody-elses-keep"})
    return Finding(
        check="published_site_nothing_builds_refused", ok=True,
        message=(f"NOT REFUSED: {len(fs)} findings, none refusing, on a "
                 f"published set containing a site this plan does not build"),
        numbers={"findings": len(fs)})


def _case_a_declared_site_missing_from_the_publication_is_refused():
    """THE POPULATION ERROR THIS CHECK ITSELF HAD: until 2026-09-22 the audit
    filtered to the TWO towns, so all fourteen outlying sites could have gone
    missing from the published set and it would have reported two green
    findings and stopped."""
    pm = _plan()
    ids = [t["id"] for t in pm.TOWNS]          # the towns only -- 14 absent
    fs = audit(_published("Pirate68", ids), "<constructed>")
    hard = [f for f in fs if not f.ok and not f.advisory]
    if hard:
        msg = "; ".join(f.message for f in hard)
        for site in ("hognest-castle", "lh-west", "tree-near"):
            assert site in msg, \
                f"the refusal must name the declared sites that are missing " \
                f"({site} absent from it): {msg[:200]}"
        return Finding(
            check="declared_site_missing_refused", ok=False,
            message=(f"a published set with all 14 declared outlying sites "
                     f"missing was refused: {msg[:120]}"),
            numbers={"published": len(ids), "declared": len(pm.OUTLIERS) + 2})
    return Finding(
        check="declared_site_missing_refused", ok=True,
        message=(f"NOT REFUSED: {len(fs)} findings, none refusing, on a "
                 f"published set missing all 14 outlying sites"),
        numbers={"findings": len(fs)})


def _case_the_published_ulfsland_set_is_accepted():
    """BOTH DIRECTIONS, and the one that matters for the gate: the site set
    this world actually publishes must NOT refuse.  Its outliers are pad
    centres that `best_pad` slid off their declared centres -- measured, 11 of
    14 differ, max 16 m -- and that is the slide, not a move."""
    pm = _plan()
    ids = ([t["id"] for t in pm.TOWNS] + [o["id"] for o in pm.OUTLIERS])
    fs = audit(_published("Pirate68", ids), "<constructed>")
    hard = [f for f in fs if not f.ok and not f.advisory]
    assert not hard, f"the complete declared set was refused: {hard[:1]}"
    assert len(fs) == len(ids), f"{len(fs)} findings for {len(ids)} records"
    return []


# ---------------------------------------------------------------------------
# THE BODY.  `OUTLIERS` names one authored body and, for lh-west, an authored
# alternate.  The fallback is scoped by the SAME declared/derived rule as the
# coordinate, because an automatic substitution on a declared site is the swap
# that was refused by hand, arrived at by machine.
# ---------------------------------------------------------------------------
ULF_LIGHT = "hs_plains_ulf_the_builder_lighthouse.blueprint"
DRAKE_LIGHT = "drake-lighthouse.blueprint"


def _verdicts(failing: set):
    """A CONSTRUCTED emitter verdict per body name -- no blueprint library, no
    subprocess, and nothing memoised."""
    def check(name):
        ok = name not in failing
        return {"name": name, "ok": ok, "commands": 1255 if ok else 0,
                "missing": [], "refusals": [], "repaired": False,
                "repairs": [], "resolved": name,
                "reason": "" if ok else
                          "unsheltered_deck: 10 deck column(s) ENCLOSED BY "
                          "WALLS and OPEN TO THE SKY (50 open of 170)"}
    return check


def _case_an_undeclared_site_takes_an_authored_alternate():
    """WHERE THE SEARCH CHOSE THE PLACE, nothing is standing and the body is
    part of the placement decision this run is making -- so a body the emitter
    refuses may fall through to the next body this project DECLARED it is
    willing to build there."""
    pm = _plan()
    picker = _enough_candidates(pm)
    placed = pm.select_outliers(picker, "Fresh42", {}, "",
                                _verdicts({ULF_LIGHT}))
    lw = next(r for r in placed if r["id"] == "lh-west")
    assert lw["body"] == DRAKE_LIGHT, \
        (f"the undeclared lh-west kept a body the emitter refuses: "
         f"{lw['body']}")
    assert "AUTHORED ALTERNATE" in lw["body_why"], lw["body_why"]
    assert lw["body_report"]["ok"], lw["body_report"]
    authored = {o["id"]: o["body"] for o in pm.OUTLIERS}
    moved = [r["id"] for r in placed
             if r["id"] != "lh-west" and r["body"] != authored[r["id"]]]
    assert not moved, \
        f"bodies the emitter accepted must not be substituted: {moved}"
    return []


def _case_a_declared_site_keeps_a_body_the_emitter_refuses():
    """AND WHERE THE SITE IS DECLARED, IT DOES NOT.  u-lhwest is published in
    sites.yaml with that body's piece count and sha256 and may be STANDING, so
    substituting it is a plan that stops describing the ground -- the refusal
    is the deliverable and the repair is to the blueprint."""
    pm = _plan()
    picker = _enough_candidates(pm, fields=dict(ULFSLAND_FIELD),
                                patch="mainland")
    anchors, source = pm.declared_outlier_sites("Pirate68")
    placed = pm.select_outliers(picker, "Pirate68", anchors, source,
                                _verdicts({ULF_LIGHT}))
    lw = next(r for r in placed if r["id"] == "lh-west")
    assert lw["body"] == ULF_LIGHT, \
        (f"a DECLARED site's body was substituted to {lw['body']}: that is the "
         f"swap refused by hand, arrived at by machine, and it makes the plan "
         f"stop describing standing ground")
    assert lw["body_report"] is None, \
        "a declared site's body is not resolved, so it has no report"
    return []


def _case_a_site_whose_every_body_refuses_is_refused():
    """AND THE REFUSAL NAMES THE WHOLE LIST: 'tried these N bodies, each with
    its measured reason', never 'no lighthouse'.  THE SITE is refused, not
    the stage (`plan.BODY_REFUSAL_DEPENDS_ON`): every other site still
    solves."""
    pm = _plan()
    picker = _enough_candidates(pm)
    placed = pm.select_outliers(picker, "Fresh42", {}, "",
                                _verdicts({ULF_LIGHT, DRAKE_LIGHT}))
    refused = [r for r in placed if r.get("refused")]
    if not refused:
        return Finding(
            check="every_body_refusing_is_refused", ok=True,
            message=("NOT REFUSED: a site was planned with a body the emitter "
                     "cannot spawn, so the plan builds something that cannot "
                     "be built"),
            numbers={"bodies_failing": 2})
    authored = {o["id"]: o for o in pm.OUTLIERS}
    for r in refused:
        site = authored[r["id"]]
        candidates = [site["body"], *site.get("alternates", ())]
        tried = [b["body"] for b in r["refused"]["bodies"]]
        assert tried == candidates, \
            (f"{r['id']}'s refusal must name every body it tried "
             f"{candidates}; it named {tried}")
        msg = r["refused"]["why"]
        assert "unsheltered_deck" in msg, \
            f"the refusal must carry each body's MEASURED reason: {msg[:240]}"
        assert "Fresh42" in msg, f"the refusal names no seed: {msg[:240]}"
    solved = [r["id"] for r in placed if not r.get("refused")]
    assert solved and all("x" in r for r in placed if not r.get("refused")), \
        f"a sibling site did not solve: {solved}"
    return Finding(
        check="every_body_refusing_is_refused", ok=False,
        message=(f"{[r['id'] for r in refused]}, whose declared bodies all "
                 f"refuse, are refused and each refusal names every body it "
                 f"tried; {len(solved)} sibling site(s) still solve"),
        numbers={"sites_refused": len(refused), "sites_solved": len(solved)})


def _neuter_body_to_the_authored_pick():
    """RESTORE THE DEFECT VERBATIM: `body` as a single authored field with no
    alternate reachable -- the state in which a fresh seed could not complete
    because one blueprint in the library has a hole in its roof."""
    pm = _plan()
    saved = pm.resolve_body

    def authored_only(rec, seed, body_ok=None):
        return rec["body"], {"ok": True}, "the authored body"

    pm.resolve_body = authored_only

    def undo():
        pm.resolve_body = saved
    return undo


def _neuter_body_substitution_unscoped():
    """RESTORE THE DEFECT THIS FIX COULD EASILY HAVE HAD INSTEAD: the naive
    (b) -- resolve the body for EVERY record, declared or not.  On Ulfsland
    that silently swaps u-lhwest's published body the first time the emitter
    refuses it."""
    pm = _plan()
    saved = pm.select_outliers

    def unscoped(picker, seed, anchors, source, body_ok=None):
        placed = saved(picker, seed, anchors, source, body_ok)
        for rec in placed:
            rec["body"], rec["body_report"], rec["body_why"] = pm.resolve_body(
                rec, seed, body_ok)
        return placed

    pm.select_outliers = unscoped

    def undo():
        pm.select_outliers = saved
    return undo


def _neuter_the_audit_to_towns_only():
    """RESTORE THE DEFECT VERBATIM: this section's own `run` as it stood until
    2026-09-22 -- records filtered to the TWO town ids, each compared to its
    declaration by coordinate, and NO membership test in either direction.  It
    judged 2 of 16 published records and was silent about the other 14.

    The body is restored rather than the population emptied: emptying
    `plan.OUTLIERS` leaves `ANCHORED_OUTLIERS` declaring 14 sites, so the
    membership test still fires and the case still refuses -- MEASURED, it
    reported NOT LOAD-BEARING, which is the harness catching a neuter that
    does not restore the defect.  What was missing was never the id set; it
    was the two membership checks.
    """
    saved = globals()["audit"]

    def towns_only(doc, where):
        pm = _plan()
        out: list[Finding] = []
        seed = doc.get("seed")
        town_ids = {t["id"] for t in pm.TOWNS}
        records = [r for r in (doc.get("sites") or [])
                   if r.get("id") in town_ids]
        if not seed:
            return out
        anchors, source = pm.declared_town_centres(str(seed))
        for r in records:
            got = (int(round(float(r["xz"][0]))), int(round(float(r["xz"][1]))))
            a = anchors.get(r["id"])
            if a is None:
                continue
            want = (int(round(float(a["xz"][0]))),
                    int(round(float(a["xz"][1]))))
            out.append(Finding(
                check="published_town_centres", ok=got == want,
                message=(f"{r['id']} published {got}, declared {want} by "
                         f"{source}"),
                numbers={"published": list(got), "declared": list(want)}))
        return out

    globals()["audit"] = towns_only

    def undo():
        globals()["audit"] = saved
    return undo


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------
def _case_fresh_seed_chooses_from_its_own_candidates():
    """THE DEFECT, DIRECTLY: a seed nobody has declared anything about must be
    able to plan its towns AT ALL."""
    pm = _plan()
    cands = _fresh_candidates()
    try:
        picks = pm.select_towns(cands, "Fresh42", {}, "")
    except Exception as exc:                                # noqa: BLE001
        raise AssertionError(
            f"a seed with no declared town centres could not choose a town at "
            f"all: {type(exc).__name__}: {exc}") from None
    got = [(p["spec"]["id"], p["site"].x, p["site"].z) for p in picks]
    assert got == [("stenvik", 1180.0, -2044.0),
                   ("vestvik", -311.0, 2570.0)], \
        f"the towns must be the search's own top-ranked districts, got {got}"
    assert all(p["declared_xz"] is None for p in picks), \
        "an undeclared centre must not be reported as declared"
    for p in picks:
        why = p["why"]
        assert "Fresh42" in why, f"{p['spec']['id']} why names no seed: {why}"
        assert "--expect-towns" in why, \
            (f"{p['spec']['id']} why does not say how to pin the centre it "
             f"just chose: {why}")
        leaked = _mentions_ulfsland(why)
        assert not leaked, \
            (f"{p['spec']['id']} on Fresh42 is justified with Ulfsland's "
             f"numbers {leaked}: {why}")
    return []


def _case_declared_centre_the_search_lost_is_refused():
    """THE REFUSAL THAT MUST SURVIVE, and the message it must carry: the
    DECLARED centre, the seed being solved, and the candidates actually
    found -- not another world's pair."""
    pm = _plan()
    cands = _fresh_candidates()
    anchors = {"stenvik": {"xz": (742, -1290), "why": "DECLARED for Fresh42"}}
    source = "--expect-towns on this run"
    try:
        picks = pm.select_towns(cands, "Fresh42", anchors, source)
    except (ValueError, SystemExit) as exc:
        msg = str(exc)
        assert "742" in msg and "-1290" in msg, \
            (f"the refusal must name the centre DECLARED for this seed; it "
             f"said: {msg[:240]}")
        assert "Fresh42" in msg, \
            f"the refusal must name the seed it was solving: {msg[:240]}"
        missing = [f"{s.x:.1f}" for s in cands if f"{s.x:.1f}" not in msg]
        assert not missing, \
            (f"the refusal must name the candidates the search DID return, so "
             f"the operator can re-declare; it omitted {missing}: {msg[:240]}")
        leaked = _mentions_ulfsland(msg)
        assert not leaked, \
            (f"the refusal named another world's coordinates {leaked} to an "
             f"operator solving Fresh42: {msg[:240]}")
        return Finding(
            check="declared_town_centre_lost_is_refused", ok=False,
            message=(f"a Fresh42 solve whose declared stenvik centre "
                     f"(742, -1290) is not a candidate was refused, naming its "
                     f"own 3 candidates: {msg[:120]}"),
            numbers={"declared": [742, -1290], "candidates": len(cands),
                     "seed": "Fresh42"})
    moved = [(p["spec"]["id"], p["site"].x, p["site"].z) for p in picks]
    return Finding(
        check="declared_town_centre_lost_is_refused", ok=True,
        message=(f"NOT REFUSED: stenvik was declared at (742, -1290) and "
                 f"silently planned somewhere else -- {moved} -- and "
                 f"sites.yaml would publish the new centre as the town RoadNet "
                 f"builds to"),
        numbers={"declared": [742, -1290], "planned": moved[0][1:]})


def _case_too_little_land_stops_with_its_own_numbers():
    """A SEED WHOSE GROUND CANNOT CARRY THE PLAN, which is a refusal and not a
    crash: the stop must say how many districts the search found on THIS seed
    and how many towns the plan wants."""
    pm = _plan()
    cands = [_candidate(1180.0, -2044.0, "mainland")]
    try:
        picks = pm.select_towns(cands, "Fresh42", {}, "")
    except (ValueError, SystemExit) as exc:
        msg = str(exc)
        assert "Fresh42" in msg, f"the stop names no seed: {msg[:240]}"
        assert "1 town district candidate" in msg, \
            (f"the stop must count what the search found on this seed: "
             f"{msg[:240]}")
        assert "stenvik" in msg and "vestvik" in msg, \
            f"the stop must name the towns it cannot place: {msg[:240]}"
        leaked = _mentions_ulfsland(msg)
        assert not leaked, \
            (f"the stop named another world's coordinates {leaked}: "
             f"{msg[:240]}")
        return Finding(
            check="not_enough_districts_is_refused", ok=False,
            message=(f"a Fresh42 solve with 1 district candidate and 2 towns "
                     f"was refused: {msg[:120]}"),
            numbers={"candidates": 1, "towns": len(pm.TOWNS)})
    return Finding(
        check="not_enough_districts_is_refused", ok=True,
        message=(f"NOT REFUSED: {len(picks)} towns were planned from 1 "
                 f"district candidate, so at least one town stands where the "
                 f"district search rejected the ground"),
        numbers={"candidates": 1, "planned": len(picks)})


def _case_a_declared_centre_outranked_does_not_move():
    """A DECLARATION BINDS BY COORDINATE, NOT BY RANK.  If it bound by rank,
    any change to `sites.Picker.town`'s scoring would relocate a standing town
    while every declaration still 'matched'."""
    pm = _plan()
    cands = _fresh_candidates()
    cands.insert(1, _candidate(742.0, -1290.0, "mainland", relief=10.1))
    anchors = {"stenvik": {"xz": (742, -1290), "why": "DECLARED for Fresh42"}}
    picks = pm.select_towns(cands, "Fresh42", anchors,
                            "--expect-towns on this run")
    got = {p["spec"]["id"]: (p["site"].x, p["site"].z) for p in picks}
    assert got["stenvik"] == (742.0, -1290.0), \
        (f"the DECLARED stenvik centre (742, -1290) was outranked by "
         f"{cands[0].x, cands[0].z} and the town MOVED to {got['stenvik']}")
    assert got["vestvik"] == (1180.0, -2044.0), \
        (f"the undeclared town must take the best candidate the declared one "
         f"did not, got {got['vestvik']}")
    assert picks[0]["declared_xz"] == [742.0, -1290.0], picks[0]["declared_xz"]
    return []


def _case_the_default_seeds_declaration_is_the_pair_it_publishes():
    """THE DEFAULT PATH, WHICH MUST NOT MOVE.  `settlements/sites.yaml` in this
    tree publishes stenvik (496.0, 904.0) and vestvik (-906.0, 1846.0) with two
    `why` strings full of measured Pirate68 numbers, and RoadNet's node set is
    built from them.  The candidate order here is DELIBERATELY the wrong way
    round, so a selection that had gone back to rank order would put stenvik on
    the west isle."""
    pm = _plan()
    cands = [_candidate(-906.0, 1846.0, "westisle", relief=13.3, meadows=1.0),
             _candidate(496.0, 904.0, "mainland", relief=17.8, meadows=0.998),
             _candidate(-262.0, 1998.0, "westisle", relief=18.5, meadows=1.0)]
    anchors, source = pm.declared_town_centres("Pirate68")
    assert "ANCHORED_TOWNS" in source, source
    picks = pm.select_towns(cands, "Pirate68", anchors, source)
    got = {p["spec"]["id"]: (p["site"].x, p["site"].z) for p in picks}
    assert got == {"stenvik": (496.0, 904.0), "vestvik": (-906.0, 1846.0)}, got
    why = {p["spec"]["id"]: p["why"] for p in picks}
    assert why["stenvik"].startswith(
        "MEASURED: a 176 m district of 99.8% Meadows at 45.57 m"), \
        f"the published stenvik justification changed: {why['stenvik'][:120]}"
    assert why["vestvik"].startswith(
        "MEASURED: 100% Meadows at 33.99 m"), \
        f"the published vestvik justification changed: {why['vestvik'][:120]}"
    return []


# A DERIVED field's windows are named for what they DO.  MEASURED on
# /tmp/seedfield/fresh42_1m.bin: trunk-primary, trunk-secondary and seven
# outlier-* ids, and not one of them is called mainland or westisle -- those
# two names exist only inside the Pirate68 binary on disk.
DERIVED_FIELD = {"trunk-primary": None, "trunk-secondary": None,
                 "outlier-plains215": None, "outlier-mistlands82": None}
ULFSLAND_FIELD = {"mainland": None, "westisle": None, "mtn7114": None,
                  "south33": None}


def _case_town_patches_are_resolved_by_role():
    """WHERE A TOWN IS ALLOWED TO STAND, by the window's JOB and not by
    Ulfsland's landmass names.  `Picker.town` defaulted to
    ("mainland", "westisle"), so on a derived field the district search ran
    over the EMPTY SET and returned zero candidates -- and then the town
    selection refused, which reads exactly like a seed with no good ground."""
    pm = _plan()
    got = pm.S.trunk_patches(DERIVED_FIELD)
    assert got == ("trunk-primary", "trunk-secondary"), \
        (f"a derived field's trunk windows must be searched; got {got} for "
         f"{sorted(DERIVED_FIELD)}")
    legacy = pm.S.trunk_patches(ULFSLAND_FIELD)
    assert legacy == ("mainland", "westisle"), \
        (f"Ulfsland's binary must resolve to the same two patches in the same "
         f"order, or the published site set moves; got {legacy}")
    return []


def _case_a_field_with_no_trunk_window_is_refused():
    """A TOWN IS DEFINED AS STANDING ON A TRUNK LANDMASS.  Searching whatever
    windows happen to be in the file instead would plant a town on an outlier
    island the road network has no way to reach."""
    pm = _plan()
    fields = {"outlier-plains215": None, "outlier-mistlands82": None}
    try:
        got = pm.S.trunk_patches(fields)
    except (ValueError, SystemExit) as exc:
        msg = str(exc)
        assert "outlier-plains215" in msg, \
            f"the refusal must name what the field DOES hold: {msg[:200]}"
        assert "trunk-primary" in msg, \
            f"the refusal must name the window role it wanted: {msg[:200]}"
        return Finding(
            check="no_trunk_window_is_refused", ok=False,
            message=(f"a field of 2 outlier windows and no trunk window was "
                     f"refused: {msg[:120]}"),
            numbers={"windows": sorted(fields)})
    return Finding(
        check="no_trunk_window_is_refused", ok=True,
        message=(f"NOT REFUSED: the district search was pointed at {got} -- "
                 f"windows that are not a trunk landmass -- so a town could be "
                 f"planned where no trunk road reaches"),
        numbers={"searched": list(got)})


# ---------------------------------------------------------------------------
# THE OUTLYING SITES.  Same cut, one layer down, and it is where the town fix
# was MEASURED to stop: with towns resolved, a real derived field for another
# seed got through town selection and then raised `KeyError: 'mainland'` at
# plan.py's first outlier, because 14 of 14 records named a window that seed
# does not have.
# ---------------------------------------------------------------------------
class _StubField:
    """A window with an id, a centre and bounds, and nothing else: enough for
    `plan.patch_containing` and for `terrain1m.field_for`, which `dedupe`
    consults to settle a point that two overlapping windows both contain."""

    def __init__(self, fid, x0, x1, z0, z1):
        self.id = fid
        self.box = (x0, x1, z0, z1)
        self.cx = (x0 + x1) / 2.0
        self.cz = (z0 + z1) / 2.0
        self.half = (x1 - x0) / 2.0
        self.step = 1.0

    def contains(self, x, z, pad_m=0.0):
        x0, x1, z0, z1 = self.box
        return (x0 + pad_m <= x <= x1 - pad_m
                and z0 + pad_m <= z <= z1 - pad_m)


def _boxes(names) -> dict:
    """A field dict of `_StubField` windows, one per id, all covering the same
    generous box -- the ids are what these cases are about, not the geometry."""
    return {n: _StubField(n, -9000.0, 9000.0, -9000.0, 9000.0) for n in names}


class _StubPicker:
    """A Picker that answers the four kind searches with CONSTRUCTED sites.

    `select_outliers` is given a picker and calls `KIND_SEARCH[kind](picker)`
    and `sites.dedupe`, both of which read only `fields` and the candidates
    themselves -- so the whole selection can be driven without a field file,
    which is the point: a fixture that needed Pirate68's 230 MB binary would
    be testing seed-independence on Pirate68.
    """

    def __init__(self, per_kind: dict, fields: dict | None = None):
        self.per_kind = per_kind
        self.fields = (_boxes(fields) if fields is not None
                       else _boxes(("trunk-primary",)))

    def castle(self, **k):
        return list(self.per_kind.get("castle", ()))

    def watchtower(self, **k):
        return list(self.per_kind.get("watchtower", ()))

    def lighthouse(self, **k):
        return list(self.per_kind.get("lighthouse", ()))

    def treehouse(self, **k):
        return list(self.per_kind.get("treehouse", ()))


def _kind_site(kind: str, x: float, z: float, patch: str = "trunk-primary"):
    S = _plan().S
    return S.Site(
        kind=kind, x=x, z=z, patch=patch, ground_y=77.5, biome="BlackForest",
        pad={"x": x, "z": z, "width_m": 20.0, "depth_m": 20.0,
             "target_y": 77.5, "moved_m3": 0.0, "max_cut_m": 0.0,
             "max_fill_m": 0.0, "clamp_ok": True, "clamp_headroom_m": 8.0,
             "wet_samples": 0, "samples": 400, "biome_counts": {}},
        rise_m=31.1, dist_water_m=30.6, dist_spawn_m=733.0,
        nearest_location={"prefab": "Runestone", "dist_m": 210.0}, notes={})


def _enough_candidates(pm, fields=None, patch: str = "trunk-primary"):
    """One candidate per authored record of each kind, 200 m apart so
    `dedupe`'s 40 m collapse does not eat them, and all in `patch` so
    `dedupe`'s own patch test keeps them."""
    per: dict[str, list] = {}
    for i, o in enumerate(pm.OUTLIERS):
        per.setdefault(o["kind"], []).append(
            _kind_site(o["kind"], 1000.0 + 200.0 * i, -2000.0 - 200.0 * i,
                       patch))
    return _StubPicker(per, fields)


def _case_undeclared_outliers_come_from_their_own_kinds_search():
    """A seed with no declaration must still get its castle, its towers, its
    lights and its hamlets -- from ITS OWN ground, each from the search that
    defines that kind."""
    pm = _plan()
    picker = _enough_candidates(pm)
    try:
        placed = pm.select_outliers(picker, "Fresh42", {}, "")
    except Exception as exc:                                # noqa: BLE001
        raise AssertionError(
            f"a seed with no declared outlying sites could not place one at "
            f"all: {type(exc).__name__}: {exc}") from None
    assert len(placed) == len(pm.OUTLIERS), \
        f"{len(placed)} placed for {len(pm.OUTLIERS)} authored records"
    for rec in placed:
        assert rec["centre_declared"] is False, rec["id"]
        assert rec["patch"] == "trunk-primary", \
            (f"{rec['id']} was placed in {rec['patch']!r}; a site's window is "
             f"the one its candidate was found in")
        leaked = _mentions_ulfsland(rec["why"])
        assert not leaked, \
            f"{rec['id']} on Fresh42 is justified with Ulfsland's numbers {leaked}"
        assert "Fresh42" in rec["why"], rec["why"][:120]
    ranks = [r["search_rank"] for r in placed if r["kind"] == "watchtower"]
    assert ranks == list(range(len(ranks))), \
        f"the {len(ranks)} watchtowers must take distinct candidates, got {ranks}"
    return []


def _case_a_kind_with_too_few_candidates_is_refused():
    """AN EMPTY CANDIDATE SET IS NOT AN ANSWER.  This is the failure the whole
    item is about one layer down: a selector reading a hardcoded window name
    returned [] and 'this seed has nowhere for a lighthouse' was
    indistinguishable from 'I searched a window that does not exist'."""
    pm = _plan()
    picker = _enough_candidates(pm)
    picker.per_kind["lighthouse"] = []
    try:
        placed = pm.select_outliers(picker, "Fresh42", {}, "")
    except (ValueError, SystemExit) as exc:
        msg = str(exc)
        assert "Fresh42" in msg and "lighthouse" in msg, msg[:240]
        assert "0 lighthouse candidate" in msg, \
            f"the refusal must count what the search found: {msg[:240]}"
        assert "lh-south" in msg, \
            f"the refusal must name the sites it cannot place: {msg[:240]}"
        return Finding(
            check="kind_with_no_candidates_is_refused", ok=False,
            message=(f"a Fresh42 solve with 0 lighthouse candidates and 3 "
                     f"lighthouses was refused: {msg[:120]}"),
            numbers={"kind": "lighthouse", "candidates": 0})
    except Exception as exc:                                # noqa: BLE001
        # A CRASH IS NOT A REFUSAL.  `KeyError: 'mainland'` is what the old
        # table did here: an indexing failure naming no remedy, which is the
        # ACCEPT direction for this check.
        return Finding(
            check="kind_with_no_candidates_is_refused", ok=True,
            message=(f"NOT REFUSED: the selection raised "
                     f"{type(exc).__name__}: {str(exc)[:80]} instead of "
                     f"refusing with the kind, the count and the sites"),
            numbers={"kind": "lighthouse", "raised": type(exc).__name__})
    return Finding(
        check="kind_with_no_candidates_is_refused", ok=True,
        message=(f"NOT REFUSED: {len(placed)} sites planned with 0 lighthouse "
                 f"candidates, so a light stands where its own search found "
                 f"nothing"),
        numbers={"kind": "lighthouse", "planned": len(placed)})


def _case_a_declared_site_keeps_its_declared_place():
    """THE DEFAULT PATH, ONE LAYER DOWN.  Ulfsland's 14 outlying sites are
    STANDING: their coordinates are what the ledger, the roads and the portal
    tags were built against, so a declared site is taken exactly where it is
    declared and its window name is resolved, never re-searched."""
    pm = _plan()
    # Candidates ON ULFSLAND'S OWN WINDOW, so a selection that ignored the
    # declaration would succeed and move all 14 -- which is what the neuter
    # does and what this case must catch.
    picker = _enough_candidates(pm, fields=dict(ULFSLAND_FIELD),
                                patch="mainland")
    anchors, source = pm.declared_outlier_sites("Pirate68")
    assert "ANCHORED_OUTLIERS" in source, source
    placed = pm.select_outliers(picker, "Pirate68", anchors, source)
    got = {r["id"]: (r["x"], r["z"], r["patch"]) for r in placed}
    assert got["wt-spawn"] == (110.0, -104.0, "mainland"), got["wt-spawn"]
    assert got["hognest-castle"] == (530.0, 1348.0, "mainland"), \
        got["hognest-castle"]
    assert got["lh-west"] == (-1350.0, 2358.0, "westisle"), got["lh-west"]
    assert got["sudrberg-keep"] == (2406.0, -3354.0, "south33"), \
        got["sudrberg-keep"]
    assert all(r["centre_declared"] for r in placed), \
        "every Pirate68 site is declared and must say so"
    assert placed[0]["why"].startswith("MEASURED: 110.95 m pad height"), \
        f"the published hognest justification changed: {placed[0]['why'][:120]}"
    return []


def _case_a_declared_window_this_field_lacks_is_refused():
    """`patch='mainland'` IS THE SAME KIND OF THING AS x AND z.  A declaration
    naming a window the loaded field does not have must refuse by name, not
    raise KeyError from an indexing expression 40 lines later -- which is how
    this was found, on a real derived field."""
    pm = _plan()
    picker = _enough_candidates(pm, fields=dict(DERIVED_FIELD))
    anchors = {"wt-spawn": {"xz": (110.0, -104.0), "patch": "south33",
                            "why": "DECLARED"}}
    try:
        placed = pm.select_outliers(picker, "Fresh42", anchors, "a test")
    except (ValueError, SystemExit) as exc:
        msg = str(exc)
        assert "south33" in msg, f"the refusal must name the window: {msg[:240]}"
        assert "trunk-primary" in msg, \
            f"the refusal must name what the field DOES hold: {msg[:240]}"
        return Finding(
            check="declared_window_absent_is_refused", ok=False,
            message=(f"a site declared in window 'south33' on a field holding "
                     f"trunk-* was refused: {msg[:120]}"),
            numbers={"declared_patch": "south33",
                     "field": sorted(DERIVED_FIELD)})
    except Exception as exc:                                # noqa: BLE001
        # `KeyError: 'mainland'` verbatim: an indexing failure 40 lines from
        # the table that declared it, naming neither the site nor a remedy.
        # NOT a refusal.
        return Finding(
            check="declared_window_absent_is_refused", ok=True,
            message=(f"NOT REFUSED: the selection raised "
                     f"{type(exc).__name__}: {str(exc)[:80]} instead of "
                     f"naming the site, the window and what the field holds"),
            numbers={"declared_patch": "south33",
                     "raised": type(exc).__name__})
    return Finding(
        check="declared_window_absent_is_refused", ok=True,
        message=(f"NOT REFUSED: {len(placed)} sites placed with one declared "
                 f"in a window this field does not hold"),
        numbers={"declared_patch": "south33"})


def _neuter_to_the_records_own_patch_literal():
    """RESTORE THE DEFECT VERBATIM: `settlements/plan.py::build` as it stood
    before 2026-09-22 -- every OUTLIERS record carrying its own x, z and
    `patch="mainland"`, taken straight, with no search and no resolution."""
    pm = _plan()
    saved = pm.select_outliers
    frozen = pm.ANCHORED_OUTLIERS["Pirate68"]

    def literal(picker, seed, anchors, source):
        out = []
        for o in pm.OUTLIERS:
            a = frozen[o["id"]]
            rec = dict(o)
            rec.update({"x": a["xz"][0], "z": a["xz"][1], "patch": a["patch"],
                        "why": a["why"], "centre_declared": True,
                        "search_rank": None})
            # `picker.fields[o["patch"]]` is what build() did next, and on any
            # field but Pirate68's this is the KeyError.
            picker.fields[rec["patch"]]
            out.append(rec)
        return out

    pm.select_outliers = literal

    def undo():
        pm.select_outliers = saved
    return undo


def _neuter_outliers_to_search_only():
    """RESTORE THE DEFECT THIS FIX COULD EASILY HAVE INSTEAD: re-search every
    site on every run and let the declaration be decoration.  That is the
    other way to make a fresh seed work, and on Ulfsland it silently moves 14
    sites that are STANDING -- with their portals, their ledger records and
    the roads that reach them."""
    pm = _plan()
    saved = pm.select_outliers

    def search_only(picker, seed, anchors, source):
        return saved(picker, seed, {}, "")

    pm.select_outliers = search_only

    def undo():
        pm.select_outliers = saved
    return undo



# ---------------------------------------------------------------------------
# neuters: the known-bad states, restored
# ---------------------------------------------------------------------------
def _neuter_to_the_hardcoded_pair():
    """RESTORE THE DEFECT VERBATIM: `settlements/plan.py::build` as it stood
    before 2026-09-21 -- two Ulfsland coordinates inline, looked up by rounded
    xz, refusing every seed whose ground does not have them."""
    pm = _plan()
    saved = pm.select_towns

    def hardcoded(candidates, seed, anchors, source):
        by_xz = {(round(s.x), round(s.z)): s for s in candidates}
        want = {"stenvik": (496, 904), "vestvik": (-906, 1846)}
        picks = []
        for spec in pm.TOWNS:
            site = by_xz.get(want[spec["id"]])
            if site is None:
                raise ValueError(
                    f"the recorded {spec['id']} centre {want[spec['id']]} "
                    f"is no longer a town candidate: the search moved, and "
                    f"a plan that silently relocates a town is worse than "
                    f"one that stops. Candidates: {sorted(by_xz)}")
            picks.append({"spec": spec, "site": site, "rank": 0,
                          "why": spec.get("why", ""),
                          "declared_xz": list(want[spec["id"]])})
        return picks

    pm.select_towns = hardcoded

    def undo():
        pm.select_towns = saved
    return undo


def _neuter_to_rank_only():
    """RESTORE THE DEFECT THIS FIX COULD EASILY HAVE INSTEAD: take the search's
    top N and let the declaration be decoration.  That is the OTHER way to make
    a fresh seed work, and it silently relocates a standing town the first time
    the scoring changes -- which is exactly what the old constant existed to
    prevent."""
    pm = _plan()
    saved = pm.select_towns

    def rank_only(candidates, seed, anchors, source):
        return [{"spec": t, "site": candidates[i], "rank": i,
                 "why": f"the #{i + 1} candidate on seed {seed}",
                 "declared_xz": None}
                for i, t in enumerate(pm.TOWNS)]

    pm.select_towns = rank_only

    def undo():
        pm.select_towns = saved
    return undo


def _neuter_to_ulfslands_landmass_names():
    """RESTORE THE DEFECT VERBATIM: `sites.Picker.town`'s default
    `patches=("mainland", "westisle")` -- Ulfsland's two landmass names, which
    are the ids inside ONE binary on this host and are produced by no
    derivation anywhere."""
    pm = _plan()
    saved = pm.S.trunk_patches

    def legacy(fields):
        return ("mainland", "westisle")

    pm.S.trunk_patches = legacy

    def undo():
        pm.S.trunk_patches = saved
    return undo


# ---------------------------------------------------------------------------
# THE SITE BILL IS THE WRITE.  `site_finder.SITE_BILL_SURFACE_DEPENDS_ON` =
# (engine_surface, written_samples): a pad the emitter will write is billed on
# the surface `ApplyToHeightmap` adds the delta to, over the samples
# `flatten.build` stamps.  MEASURED 2026-09-24 on Fresh42: the plan billed
# sudrberg-keep over_clamp 0 / fill 6.53 m on the finding field and the write
# bills it over_clamp 279 / fill 17.403 m -- two zones with BlackForest west
# corners over a Mountain interior, 11.057 m apart under the pad.  Ulfsland's
# floating wt-peak is the same shape at 8.807 m.
#
# THE FIXTURES ARE plan.site_bill_fixture: ground CONSTRUCTED in memory from
# the IL's two height rules, never a field file or a scan.
# ---------------------------------------------------------------------------
def _bill_finding(check: str, b: dict, what: str) -> Finding:
    return Finding(
        check=check, ok=b["verdict"] == "clear",
        message=(f"{what}: {b['verdict']} on {b['surface']} -- samples "
                 f"{b.get('samples')} over_clamp {b.get('over_clamp')} fill "
                 f"{b.get('max_fill_m')} m cut {b.get('max_cut_m')} m at "
                 f"target {b.get('target_y')}"),
        where=tuple(b["pads"][0]),
        numbers={k: b.get(k) for k in ("verdict", "samples", "over_clamp",
                                        "max_fill_m", "max_cut_m",
                                        "target_y", "unread_zones")})


def _case_the_engine_surface_refuses_sudrberg_keep():
    """THE KNOWN-BAD STATE: two zones whose west corners are BlackForest over
    an all-Mountain interior.  The finding field reads Mountain; the engine
    blends the lower ground in, and the pad cannot be levelled in the clamp."""
    pm = _plan()
    fx = pm.site_bill_fixture("sudrberg-keep")
    b = pm.site_bill(fx["surface"], fx["pads"], fx["w"], fx["d"],
                     field=fx["field"])
    return _bill_finding("site_bill_surface", b, "sudrberg-keep")


def _case_the_hognest_bill_is_the_write():
    """THE SURVIVOR, AS A VERDICT: on the engine surface this module's bill
    and `flatten.build`'s agree on over_clamp, fill and cut to 1 mm -- which
    an instrument that read nothing cannot do."""
    pm = _plan()
    got = pm.bill_against_the_write(pm.site_bill_fixture("hognest-castle"))
    m, w = got["mine"], got["write"] or {}
    return Finding(
        check="site_bill_is_the_write",
        ok=bool(got["agree"]) and m["verdict"] == "clear",
        message=(f"hognest-castle: bill {m['verdict']} fill "
                 f"{m.get('max_fill_m')} cut {m.get('max_cut_m')} over_clamp "
                 f"{m.get('over_clamp')}; write fill {w.get('max_fill_m')} "
                 f"cut {w.get('max_cut_m')} over_clamp {w.get('over_clamp')}"),
        where=tuple(m["pads"][0]),
        numbers={"agree": got["agree"], "bill_fill_m": m.get("max_fill_m"),
                 "write_fill_m": w.get("max_fill_m"),
                 "bill_cut_m": m.get("max_cut_m"),
                 "write_cut_m": w.get("max_cut_m")})


def _case_a_yawed_pad_is_billed_on_what_is_stamped():
    """THE POPULATION: a 26.1 m pad at yaw 30 on a field lattice 0.4 m off
    the TerrainComp one.  MEASURED on Fresh42's stenvik-hall-1: the rotated
    rectangle billed cut 1.28 m, the stamp writes 1.872 m, on ground where
    the two surfaces agree to the millimetre."""
    pm = _plan()
    got = pm.bill_against_the_write(pm.site_bill_fixture("stenvik-hall-1"))
    m, w = got["mine"], got["write"] or {}
    return Finding(
        check="site_bill_is_the_write", ok=bool(got["agree"]),
        message=(f"stenvik-hall-1: bill samples {m.get('samples')} fill "
                 f"{m.get('max_fill_m')} cut {m.get('max_cut_m')}; write "
                 f"samples {w.get('samples')} fill {w.get('max_fill_m')} cut "
                 f"{w.get('max_cut_m')}"),
        where=tuple(m["pads"][0]),
        numbers={"agree": got["agree"], "bill_samples": m.get("samples"),
                 "write_samples": w.get("samples"),
                 "bill_fill_m": m.get("max_fill_m"),
                 "write_fill_m": w.get("max_fill_m")})


def _case_an_unread_engine_surface_is_refused():
    """UNREAD IS NOT CLEAR.  A pad on zones the engine surface never read is
    a refusal with the zones named, never a bill."""
    pm = _plan()
    fx = pm.site_bill_fixture("hognest-castle")
    b = pm.site_bill(pm.SF.EngineSurface("fixture", pm.SF.SITE_BILL_SURFACE),
                     fx["pads"], fx["w"], fx["d"], field=fx["field"])
    return _bill_finding("site_bill_unread", b, "hognest-castle, zones unread")


def _neuter_drop(term: str):
    """RESTORE ONE HALF OF THE DEFECT: drop `term` from the declaration, so
    the bill is made on the finding field (`engine_surface`) or over
    `terrain1m.pad_bill`'s rotated rectangle (`written_samples`)."""
    def neuter():
        pm = _plan()
        saved = pm.SF.SITE_BILL_SURFACE_DEPENDS_ON
        pm.SF.SITE_BILL_SURFACE_DEPENDS_ON = tuple(t for t in saved if t != term)

        def undo():
            pm.SF.SITE_BILL_SURFACE_DEPENDS_ON = saved
        return undo
    return neuter


def _neuter_engine_surface_reads_nothing():
    """THE EMPTIED INSTRUMENT: an engine surface that holds no zone.  The
    loss arm above is ALSO what an index returning nothing produces, so the
    survivor must refuse under it."""
    pm = _plan()
    saved = pm.SF.EngineSurface.missing
    pm.SF.EngineSurface.missing = lambda self, zones: sorted(set(zones))

    def undo():
        pm.SF.EngineSurface.missing = saved
    return undo


# ---------------------------------------------------------------------------
# ONE SITE'S GROUND IS ONE SITE'S GROUND.  settlements/plan.py
# SITE_CLAIM_DEPENDS_ON = (every_kind, written_samples, bodies).
#
# MEASURED 2026-09-24: lh-west and tree-near stood 5.7 m apart in the first
# Fresh42 plan, 678 lattice samples written by both, because nothing compared
# a lighthouse with a treehouse -- `sites.dedupe` collapses
# candidates of ONE kind and `certify_plan` billed every unit alone.  The
# re-solve put them 22.4 m apart by luck, and tree-near-3 still wrote 24 of
# lh-west's samples, 1.69 m under it.
#
# THE FIXTURES ARE plan.site_claim_fixture: records CONSTRUCTED in memory,
# their units derived by build.unit_records, their samples by the writer's
# own stamp.  The solver case drives plan.select_outliers over the candidate
# sets constructed above.
# ---------------------------------------------------------------------------
def _claim_finding(name: str) -> Finding:
    pm = _plan()
    fx = pm.site_claim_fixture(name)
    hits = fx["claims"].conflicts(fx["candidate"])
    c = fx["candidate"]
    u0 = c["units"][0]
    return Finding(
        check="site_claim", ok=not hits,
        message=(f"{name}: {c['site']} ({c['kind']}) at ({u0['x']}, "
                 f"{u0['z']}) "
                 + ("claims no ground another write claims" if not hits
                    else "is REFUSED: " + pm.claim_brief(hits))),
        where=(u0["x"], u0["z"]),
        numbers={"conflicts": len(hits),
                 "shared_samples": sum(h.get("shared_samples") or 0
                                       for h in hits),
                 "claimed_by": sorted({str(h.get("against")) for h in hits})})


def _case_the_5_7_m_pair_is_refused():
    """THE KNOWN-BAD STATE: the first Fresh42 plan's lh-west (lighthouse,
    22 m pad) and tree-near (treehouse cluster) 5.7 m apart."""
    return _claim_finding("lh-west+tree-near@5.7m")


def _case_every_written_unit_is_claimed():
    """THE POPULATION OF ONE SITE: a lighthouse clear of the treehouse
    cluster's searched CENTRE pad by 1.5 m and inside the stamp of the member
    the writer puts on that side."""
    return _claim_finding("cluster-member")


def _case_a_yawed_body_is_claimed():
    """THE BODY: stenvik-hall-1, 20.1 m square at yaw 30 on its recorded
    26.1 m axis-aligned stamp, so a corner stands 0.68 m off its own ground;
    a watchtower stamped one sample beyond that stamp levels under it."""
    return _claim_finding("yawed-body")


def _case_far_apart_sites_are_accepted():
    """THE SURVIVOR, AS A VERDICT: the same lighthouse and treehouse 200 m
    apart both stand."""
    return _claim_finding("far-apart")


def _case_the_solver_moves_off_claimed_ground():
    """THE SOLVER, NOT THE RULE: `select_outliers` is handed, as tree-near's
    first candidate, a treehouse 5.7 m from the lighthouse it has just placed
    as lh-west -- the first Fresh42 plan's geometry -- and a second one 200 m
    clear.  The 5.7 m candidate must not stand; the move is the search's
    next candidate, never a coordinate written here."""
    pm = _plan()
    picker = _enough_candidates(pm)
    lights = [o["id"] for o in pm.OUTLIERS if o["kind"] == "lighthouse"]
    trees = [o["id"] for o in pm.OUTLIERS if o["kind"] == "treehouse"]
    lw = picker.per_kind["lighthouse"][lights.index("lh-west")]
    near = _kind_site("treehouse", lw.x + 4.0, lw.z + 4.0)
    picker.per_kind["treehouse"].insert(trees.index("tree-near"), near)
    placed = pm.select_outliers(picker, "Fresh42", {}, "")
    tn = next(r for r in placed if r["id"] == "tree-near")
    stands = (tn["x"], tn["z"]) == (near.x, near.z)
    refused = tn.get("claim_refused") or []
    return Finding(
        check="site_claim_solver", ok=stands,
        message=(f"tree-near stands at ({tn['x']}, {tn['z']}), search rank "
                 f"{tn['search_rank']}; its first candidate ({near.x}, "
                 f"{near.z}) is 5.7 m from lh-west at ({lw.x}, {lw.z}) and "
                 + ("STANDS in the plan" if stands else
                    f"was REFUSED: {len(refused)} candidate(s) refused on "
                    f"claimed ground")),
        where=(tn["x"], tn["z"]),
        numbers={"search_rank": tn["search_rank"],
                 "claim_refused": len(refused),
                 "claimed_by": sorted({a for r in refused
                                       for a in r.get("claimed_by") or ()})})


def _neuter_claim_drop(term: str):
    """RESTORE ONE THIRD OF THE DEFECT: drop `term` from
    SITE_CLAIM_DEPENDS_ON -- the population back to one kind
    (`every_kind`), the claim back to the one pad the search slid
    (`written_samples`), or the body out of the claim (`bodies`)."""
    def neuter():
        pm = _plan()
        saved = pm.SITE_CLAIM_DEPENDS_ON
        pm.SITE_CLAIM_DEPENDS_ON = tuple(t for t in saved if t != term)

        def undo():
            pm.SITE_CLAIM_DEPENDS_ON = saved
        return undo
    return neuter


def _neuter_claim_stamps_nothing():
    """THE EMPTIED INSTRUMENT: a stamp that names no sample.  Every loss arm
    above is also what a claim index returning NOTHING produces, so the
    survivor must refuse under it -- a claim nobody could read is not clear
    ground."""
    pm = _plan()
    saved = pm.SF.written_samples
    pm.SF.written_samples = lambda *a, **k: []

    def undo():
        pm.SF.written_samples = saved
    return undo


register(Section(
    name="townsites",
    measures=("that a site's PLACE is a reading of the seed being solved -- the "
              "search's own top-ranked candidate of that kind, or a centre "
              "DECLARED for that seed -- while what to build stays authored; "
              "that a solve which cannot put a declared site where it was "
              "declared, or cannot find ground for an undeclared one, STOPS, "
              "naming the seed, the declaration and the candidates it did find; "
              "and that every pad the emitter writes is BILLED on the surface "
              "the engine levels, over the samples the writer stamps; and "
              "that no site -- of ANY kind, town building or outlier -- is "
              "placed on ground another write already claims"),
    instrument=("settlements/plan.py::select_towns, ::select_outliers, "
                "::declared_town_centres, ::declared_outlier_sites, "
                "::resolve_patch and settlements/sites.py::trunk_patches, "
                "driven over candidate sets and field-id sets constructed in "
                "this file, plus the town records of the published "
                "settlements/sites.yaml; settlements/plan.py::site_bill "
                "against terraform/flatten.py::build on ground built by "
                "::site_bill_fixture; settlements/plan.py::SiteClaims and "
                "::select_outliers over records built by "
                "::site_claim_fixture and the candidate sets above"),
    population=("the two towns in settlements/plan.py::TOWNS and the 14 "
                "outlying sites in ::OUTLIERS, every candidate handed to the "
                "selection for each of them, the patch windows each search is "
                "run over, and the town records of settlements/sites.yaml; "
                "for the bill, the lattice samples of three fixture pads "
                "(sudrberg-keep, hognest-castle, stenvik-hall-1); for the "
                "claims, every lattice sample build.unit_records' units "
                "stamp for a lighthouse, a three-member treehouse cluster, a "
                "watchtower and a yawed town hall"),
    run=run,
    cases=[
        Case("fresh_seed_chooses_from_its_own_candidates", False,
             _case_fresh_seed_chooses_from_its_own_candidates,
             prior_state=("2026-09-21: settlements/plan.py:303 held "
                          "want={'stenvik': (496, 904), 'vestvik': "
                          "(-906, 1846)} and :307 refused anything else, so "
                          "NO seed but Pirate68 could produce a sites.yaml -- "
                          "and sites.yaml supplies 16 of the road network's "
                          "nodes"),
             neuter=_neuter_to_the_hardcoded_pair,
             neuters="build()'s inline want={'stenvik': (496, 904), "
                     "'vestvik': (-906, 1846)}"),
        Case("declared_town_centre_lost_is_refused", True,
             _case_declared_centre_the_search_lost_is_refused,
             prior_state=("the refusal itself is the OLD behaviour and is "
                          "kept; what was unactionable was its message, which "
                          "named Ulfsland's two coordinates to an operator "
                          "solving another seed"),
             neuter=_neuter_to_the_hardcoded_pair,
             neuters="build()'s inline want dict, whose refusal names "
                     "(496, 904) whatever seed is being solved"),
        Case("not_enough_districts_is_refused", True,
             _case_too_little_land_stops_with_its_own_numbers,
             prior_state=("a seed whose Meadows cannot carry two districts had "
                          "no distinct outcome: it failed on the missing "
                          "(496, 904) like every other fresh seed, which says "
                          "nothing about the ground it actually has"),
             neuter=_neuter_to_the_hardcoded_pair,
             neuters="build()'s inline want dict, which cannot count "
                     "candidates"),
        Case("a_declared_centre_outranked_does_not_move", False,
             _case_a_declared_centre_outranked_does_not_move,
             prior_state=("the constant bound by COORDINATE, and that part was "
                          "right: a town that moves because the scoring "
                          "changed takes the roads and the buildings with it"),
             neuter=_neuter_to_rank_only,
             neuters="selection by search rank with the declaration ignored"),
        Case("default_seeds_declaration_is_the_pair_it_publishes", False,
             _case_the_default_seeds_declaration_is_the_pair_it_publishes,
             prior_state=("settlements/sites.yaml in this tree publishes "
                          "stenvik (496.0, 904.0) and vestvik "
                          "(-906.0, 1846.0); roads/spec.py adopts them as "
                          "network nodes"),
             neuter=_neuter_to_rank_only,
             neuters="selection by search rank, which on this fixture puts "
                     "stenvik on the west isle"),
        Case("town_patches_are_resolved_by_role", False,
             _case_town_patches_are_resolved_by_role,
             prior_state=("MEASURED 2026-09-22 on the first REAL derived field "
                          "for another seed, /tmp/seedfield/fresh42_1m.bin: "
                          "its nine windows are trunk-primary, trunk-secondary "
                          "and seven outlier-*, so sites.Picker.town's default "
                          "('mainland', 'westisle') searched NOTHING and "
                          "plan.island_membership answered "
                          "'separate landmass, portal-only' for every site "
                          "without looking at any land"),
             neuter=_neuter_to_ulfslands_landmass_names,
             neuters="Picker.town's default patches=('mainland', 'westisle')"),
        Case("no_trunk_window_is_refused", True,
             _case_a_field_with_no_trunk_window_is_refused,
             prior_state=("a field of outlier windows only returned zero town "
                          "candidates and the stop said nothing about the "
                          "windows it had searched"),
             neuter=_neuter_to_ulfslands_landmass_names,
             neuters="Picker.town's default patches=('mainland', 'westisle'), "
                     "which names two patches whatever the field holds"),
        Case("undeclared_outliers_come_from_their_own_kinds_search", False,
             _case_undeclared_outliers_come_from_their_own_kinds_search,
             prior_state=("2026-09-22: with towns resolved, a real derived "
                          "field for another seed got THROUGH town selection "
                          "and raised KeyError: 'mainland' at plan.py's first "
                          "outlier -- 14 of 14 records carried Ulfsland's "
                          "coordinates and Ulfsland's window name"),
             neuter=_neuter_to_the_records_own_patch_literal,
             neuters="every OUTLIERS record carrying its own x, z and "
                     "patch='mainland', taken straight"),
        Case("kind_with_no_candidates_is_refused", True,
             _case_a_kind_with_too_few_candidates_is_refused,
             prior_state=("the four kind searches returned [] on any field "
                          "without Ulfsland's window names, and an empty "
                          "candidate set read exactly like a seed with "
                          "nowhere to put a lighthouse"),
             neuter=_neuter_to_the_records_own_patch_literal,
             neuters="the literal record, which never asks a search and so "
                     "can never come up short"),
        Case("declared_window_absent_is_refused", True,
             _case_a_declared_window_this_field_lacks_is_refused,
             prior_state=("plan.py:534 f = picker.fields[o['patch']] raised "
                          "KeyError: 'mainland' -- an indexing expression, 40 "
                          "lines from the table that declared it, naming no "
                          "remedy"),
             neuter=_neuter_to_the_records_own_patch_literal,
             neuters="the literal record, whose window name is indexed "
                     "straight into picker.fields"),
        Case("declared_site_keeps_its_declared_place", False,
             _case_a_declared_site_keeps_its_declared_place,
             prior_state=("Ulfsland's 14 outlying sites are STANDING: their "
                          "coordinates are what the ledger, the roads and the "
                          "portal tags were built against, and re-searching "
                          "them would move a built world"),
             neuter=_neuter_outliers_to_search_only,
             neuters="re-searching every site on every run, with the "
                     "declaration ignored"),
        Case("published_site_nothing_builds_refused", True,
             _case_a_published_site_nothing_builds_is_refused,
             prior_state=("roads/spec.py::load_solved_sites adopts EVERY "
                          "record in sites.yaml as a network node, so a "
                          "record for a site no plan builds becomes a node "
                          "with no owner and an edge with no terminus"),
             neuter=_neuter_the_audit_to_towns_only,
             neuters="the audit's own population as it stood: records "
                     "filtered to the two TOWNS, 2 of 16 judged"),
        Case("declared_site_missing_refused", True,
             _case_a_declared_site_missing_from_the_publication_is_refused,
             prior_state=("MEASURED in this file on 2026-09-22: this section's "
                          "own run() filtered the published records to the two "
                          "towns, so all 14 outlying sites could have vanished "
                          "from the artefact RoadNet reads and it would have "
                          "reported two green findings and stopped -- the "
                          "population error this section exists to catch, in "
                          "the check itself"),
             neuter=_neuter_the_audit_to_towns_only,
             neuters="the audit's own population as it stood: records "
                     "filtered to the two TOWNS, 2 of 16 judged"),
        Case("the_published_declared_set_is_accepted", False,
             _case_the_published_ulfsland_set_is_accepted,
             prior_state=("the direction a refuse-everything audit passes: "
                          "11 of the 14 published outliers differ from their "
                          "declared centre by up to 16 m because best_pad "
                          "slid the pad to clear the POI stand-off, and "
                          "judging that offset would be a tolerance standing "
                          "in for a population"),
             neuter=_neuter_the_audit_to_towns_only,
             neuters="the audit's own population as it stood: 2 of 16 records "
                     "judged, so the complete set returns 2 findings"),
        Case("undeclared_site_takes_an_authored_alternate", False,
             _case_an_undeclared_site_takes_an_authored_alternate,
             prior_state=("MEASURED 2026-09-22 through the live emitter: "
                          "hs_plains_ulf_the_builder_lighthouse refuses on "
                          "unsheltered_deck on EVERY seed, because the defect "
                          "is in the blueprint -- and `body` was a single "
                          "authored field, so one hole in one roof stopped "
                          "every world this factory could ever plan"),
             neuter=_neuter_body_to_the_authored_pick,
             neuters="`body` as a single authored field with no alternate "
                     "reachable"),
        Case("declared_site_keeps_a_body_the_emitter_refuses", False,
             _case_a_declared_site_keeps_a_body_the_emitter_refuses,
             prior_state=("u-lhwest is PUBLISHED in sites.yaml with that "
                          "body's piece count and sha256 and may be standing; "
                          "a naive per-kind fallback resolved at plan time "
                          "would substitute it automatically -- the swap that "
                          "was refused by hand, arrived at by machine"),
             neuter=_neuter_body_substitution_unscoped,
             neuters="the naive (b): resolve the body for EVERY record, "
                     "declared or not"),
        Case("every_body_refusing_is_refused", True,
             _case_a_site_whose_every_body_refuses_is_refused,
             prior_state=("the gate built its rejection list from "
                          "rep['missing'], which was EMPTY for 13 of the 15 "
                          "bodies it refused, and named --drop-prefab for a "
                          "geometry preflight it had not measured"),
             neuter=_neuter_body_to_the_authored_pick,
             neuters="a resolver that returns the authored body without "
                     "asking the emitter anything"),
        Case("site_bill_refuses_sudrberg_keep", True,
             _case_the_engine_surface_refuses_sudrberg_keep,
             prior_state=("plan.Fresh42.json billed sudrberg-keep over_clamp "
                          "0 / max_fill 6.53 m on the finding field; the "
                          "write bills it over_clamp 279 / max_fill 17.403 m "
                          "on the heightmap surface, 11.057 m apart"),
             neuter=_neuter_drop("engine_surface"),
             neuters="the bill made on the finding field "
                     "(WorldGenerator.GetHeight), as plan.py did until "
                     "2026-09-24"),
        Case("site_bill_is_the_write_at_hognest", False,
             _case_the_hognest_bill_is_the_write,
             prior_state=("hognest-castle: plan 4.77 / 5.06, write-time "
                          "4.768 / -5.060 -- the control that agrees"),
             neuter=_neuter_engine_surface_reads_nothing,
             neuters="an engine surface that read no zone at all"),
        Case("site_bill_is_the_write_at_yaw_30", False,
             _case_a_yawed_pad_is_billed_on_what_is_stamped,
             prior_state=("stenvik-hall-1 on Fresh42: plan 681 samples cut "
                          "1.28 m, write 756 samples cut 1.872 m, surfaces "
                          "identical under the pad"),
             neuter=_neuter_drop("written_samples"),
             neuters="terrain1m.pad_bill's population: the finding field's "
                     "lattice inside the rotated rectangle"),
        Case("site_bill_unread_is_refused", True,
             _case_an_unread_engine_surface_is_refused,
             prior_state=("UNREAD IS NOT CLEAR: before 2026-09-24 no engine "
                          "zone was read at all and every bill came back "
                          "clean or not from the finding field"),
             neuter=_neuter_drop("engine_surface"),
             neuters="the finding field standing in for the zones nobody "
                     "read"),
        Case("site_claim_refuses_the_5_7_m_pair", True,
             _case_the_5_7_m_pair_is_refused,
             prior_state=("the first Fresh42 plan: lh-west (lighthouse) and "
                          "tree-near (treehouse) 5.66 m apart, 168 + 272 + "
                          "238 lattice samples stamped by both, and nothing "
                          "compared them: sites.dedupe "
                          "collapses candidates of ONE kind"),
             neuter=_neuter_claim_drop("every_kind"),
             neuters="the population sites.dedupe compares: claims of the "
                     "candidate's own kind only"),
        Case("site_claim_is_every_written_unit", True,
             _case_every_written_unit_is_claimed,
             prior_state=("plan.Fresh42.json as re-solved: lh-west and "
                          "tree-near 22.4 m apart, centre pads 1.0 m apart, "
                          "and tree-near-3's written stamp still sharing 24 "
                          "samples with lh-west's, 1.69 m below it"),
             neuter=_neuter_claim_drop("written_samples"),
             neuters="the claim as the one pad the search slid, at the "
                     "site's centre"),
        Case("site_claim_covers_the_body_at_its_yaw", True,
             _case_a_yawed_body_is_claimed,
             prior_state=("stenvik-hall-1 on Fresh42: a 20.1 m body at yaw 30 "
                          "needs a 27.46 m box and is stamped on 26.1 m, so "
                          "its corner stands 0.68 m off its own ground"),
             neuter=_neuter_claim_drop("bodies"),
             neuters="claims made of stamps alone, bodies ignored"),
        Case("site_claim_far_apart_sites_both_stand", False,
             _case_far_apart_sites_are_accepted,
             prior_state=("the control: the same lighthouse and treehouse "
                          "200 m apart"),
             neuter=_neuter_claim_stamps_nothing,
             neuters="a stamp that names no lattice sample, i.e. a claim "
                     "index that returned nothing"),
        Case("site_claim_solver_moves_off_claimed_ground", True,
             _case_the_solver_moves_off_claimed_ground,
             prior_state=("MEASURED 2026-09-24 against select_outliers before "
                          "the fix: tree-near was placed on its first "
                          "candidate (3004.0, -3996.0), 5.7 m from lh-west at "
                          "(3000.0, -4000.0), search rank 2, claim_refused 0"),
             neuter=_neuter_claim_drop("every_kind"),
             neuters="the solver judging a candidate against its own kind "
                     "only"),
    ],
))
