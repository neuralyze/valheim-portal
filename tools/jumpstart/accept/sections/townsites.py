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

So the comparison is against a DECLARATION -- an operator's `--expect-towns
id=x,z` for one solve -- and a seed with no declaration has nothing to be
relocated FROM: its towns are the district search's own top-ranked candidates
on its own land.  THERE IS NO STANDING DECLARATION FOR ANY SEED.  `plan.
ANCHORED_TOWNS` and `ANCHORED_OUTLIERS` pinned Pirate68's 16 sites to the places
the same search had returned on an earlier run -- frozen solver output -- and
were deleted: the only sites a person declares are `worldseed.HUMAN_SITES`
(Ulfsgard and Sudrvik), measured by `settlements/harbour_site.py` and published
under `declared_sites`.  This section is where that declaration is held to the
artefact RoadNet reads.

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
import tempfile
import types
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

    TWO POPULATIONS, JUDGED BY DIFFERENT RULES.  The SOLVER's sites (`sites`,
    every town and outlier this plan builds) are held to MEMBERSHIP: every
    published record is one this plan builds, because RoadNet adopts every
    record as a node.  Nothing declares them and nothing may: a coordinate is
    the search's own answer, and an advisory that nudged an operator to pin it
    would be the frozen-solver-output defect again.  The HUMAN-DECLARED sites
    (`declared_sites`) are held to the declaration exactly: every site
    `worldseed.HUMAN_SITES` declares for this seed is published, none that it
    does not, and each at the request that was declared -- because a published
    node for a request nobody is making is a road to nowhere, and a missing one
    is the network forgetting a city.
    """
    pm = _plan()
    ws = I._worldseed()
    out: list[Finding] = []
    seed = doc.get("seed")
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
    if not records:
        out.append(Finding(
            check="published_town_centres", ok=True, advisory=True,
            message=(f"{where} publishes no record for any of the "
                     f"{len(declared_ids)} sites this plan builds"),
            numbers={"seed": str(seed)}))
    extra = sorted(r.get("id") for r in (doc.get("sites") or [])
                   if r.get("id") not in declared_ids)
    if extra:
        out.append(Finding(
            check="published_sites_are_the_declared_set", ok=False,
            message=(f"{where} publishes {extra} for seed {seed!r}, which "
                     f"this plan does not build; RoadNet adopts every record "
                     f"in this file as a network node"),
            numbers={"seed": str(seed), "undeclared": extra}))
    want = ws.human_sites(str(seed))
    have = {d.get("id"): d for d in (doc.get("declared_sites") or [])}
    missing = sorted(set(want) - set(have))
    if missing:
        out.append(Finding(
            check="published_sites_are_the_declared_set", ok=False,
            message=(f"worldseed.HUMAN_SITES declares {missing} for seed "
                     f"{seed!r} and {where} publishes no node for them: the "
                     f"declaration and the artefact RoadNet reads disagree, so "
                     f"one of them moved without the other"),
            numbers={"seed": str(seed), "declared_not_published": missing}))
    stranger = sorted(set(have) - set(want))
    if stranger:
        out.append(Finding(
            check="published_sites_are_the_declared_set", ok=False,
            message=(f"{where} publishes declared site(s) {stranger} for seed "
                     f"{seed!r} and worldseed.HUMAN_SITES declares no such "
                     f"site: RoadNet would route to a node nobody asked for"),
            numbers={"seed": str(seed), "published_not_declared": stranger}))
    for sid, rec in want.items():
        d = have.get(sid)
        if d is None:
            continue
        asked = [float(v) for v in rec["xz"]]
        got = [float(v) for v in d.get("requested_xz") or ()]
        if got != asked:
            out.append(Finding(
                check="published_sites_are_the_declared_set", ok=False,
                message=(f"{sid} is published for the request {got} and "
                         f"worldseed.HUMAN_SITES declares {asked}: the node "
                         f"was measured for a request nobody is making"),
                numbers={"seed": str(seed), "published": got,
                         "declared": asked}))
            continue
        m = d.get("measured") or {}
        out.append(Finding(
            check="published_sites_are_the_declared_set", ok=True,
            advisory=True,
            message=(f"{sid} is declared at {tuple(asked)} by the owner and "
                     f"published as the node {tuple(d.get('xz') or ())}, the "
                     f"landing {m.get('landing_gap_m')} m from the request "
                     f"({m.get('request_water_depth_m')} m of sea over it)"),
            numbers={"seed": str(seed), "declared": asked,
                     "node": list(d.get("xz") or ()),
                     "landing_gap_m": m.get("landing_gap_m")}))
    return out


def _published(seed: str, ids, *, extra=None, declared: bool = True) -> dict:
    """A CONSTRUCTED sites.yaml document: a record at the origin for each of
    `ids` (solver sites carry no declaration, so no coordinate is judged), the
    human-declared sites `worldseed.HUMAN_SITES` declares for `seed` (unless
    `declared` is False), plus any `extra` records nothing builds."""
    ws = I._worldseed()
    sites = [{"id": i, "xz": [0.0, 0.0]} for i in ids]
    for e in (extra or ()):
        sites.append({"id": e, "xz": [0.0, 0.0]})
    pub = []
    if declared:
        for sid, rec in ws.human_sites(seed).items():
            pub.append({"id": sid, "type": rec["kind"], "node": rec["node"],
                        "requested_xz": [float(rec["xz"][0]),
                                         float(rec["xz"][1])],
                        "xz": [float(rec["xz"][0]) - 12.5,
                               float(rec["xz"][1]) + 12.5],
                        "measured": {"landing_gap_m": 17.7,
                                     "request_water_depth_m": 3.2}})
    doc = {"seed": seed, "world": None, "sites": sites}
    if pub:
        doc["declared_sites"] = pub
    return doc


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
    """THE POPULATION ERROR THIS CHECK ITSELF HAD, in its new form: the audit
    judged the solver's records and never asked whether the sites a PERSON
    declared were published, so RoadNet could have been handed a network with
    no Ulfsgard in it and this would have reported green."""
    pm = _plan()
    ids = [t["id"] for t in pm.TOWNS] + [o["id"] for o in pm.OUTLIERS]
    fs = audit(_published("Pirate68", ids, declared=False), "<constructed>")
    hard = [f for f in fs if not f.ok and not f.advisory]
    if hard:
        msg = "; ".join(f.message for f in hard)
        for site in ("harbour-city-ulfsgard", "harbour-village-sudrvik"):
            assert site in msg, \
                f"the refusal must name the declared sites that are missing " \
                f"({site} absent from it): {msg[:200]}"
        return Finding(
            check="declared_site_missing_refused", ok=False,
            message=(f"a published set carrying all {len(ids)} solver sites "
                     f"and none of the 2 human-declared sites was refused: "
                     f"{msg[:120]}"),
            numbers={"published": len(ids), "declared": 2})
    return Finding(
        check="declared_site_missing_refused", ok=True,
        message=(f"NOT REFUSED: {len(fs)} findings, none refusing, on a "
                 f"published set missing both human-declared sites"),
        numbers={"findings": len(fs)})


def _case_a_declared_site_at_another_request_is_refused():
    """A NODE MEASURED FOR A REQUEST NOBODY IS MAKING.  The declaration moved
    (Sudrvik re-declared) and the published node did not: stale, and a road
    would be routed to where the owner no longer asked for it."""
    pm = _plan()
    ids = [t["id"] for t in pm.TOWNS] + [o["id"] for o in pm.OUTLIERS]
    doc = _published("Pirate68", ids)
    doc["declared_sites"][1]["requested_xz"] = [1192.0, 444.0]
    fs = audit(doc, "<constructed>")
    hard = [f for f in fs if not f.ok and not f.advisory]
    if hard:
        msg = "; ".join(f.message for f in hard)
        assert "harbour-village-sudrvik" in msg and "1192" in msg, msg[:240]
        return Finding(
            check="declared_site_stale_request_refused", ok=False,
            message=f"a node measured for another request was refused: {msg[:120]}",
            numbers={"declared": [1190, 444], "published": [1192, 444]})
    return Finding(
        check="declared_site_stale_request_refused", ok=True,
        message=(f"NOT REFUSED: {len(fs)} findings, none refusing, on a "
                 f"published node whose request is not the declared one"),
        numbers={"findings": len(fs)})


def _case_the_published_ulfsland_set_is_accepted():
    """BOTH DIRECTIONS, and the one that matters for the gate: the site set a
    seed publishes -- every solver site AND every site a person declared for
    it -- must NOT refuse, and every declared site must be REPORTED, so a
    publication whose declared half went missing is a different count."""
    pm = _plan()
    ids = [t["id"] for t in pm.TOWNS] + [o["id"] for o in pm.OUTLIERS]
    fs = audit(_published("Pirate68", ids), "<constructed>")
    hard = [f for f in fs if not f.ok and not f.advisory]
    assert not hard, f"the complete declared set was refused: {hard[:1]}"
    for site in ("harbour-city-ulfsgard", "harbour-village-sudrvik"):
        assert any(site in f.message for f in fs), \
            f"{site} is declared, published and was not reported: " \
            f"{[f.message[:60] for f in fs]}"
    return []


def _case_the_default_seed_declares_two_sites_and_pins_nothing():
    """THE OWNER'S RULING, as an invariant: the pinned human-defined locations
    are ULFSGARD (1050, 720) and SUDRVIK (1190, 444) and NOTHING ELSE.  No town
    centre and no outlying site is declared for any seed, so the towns are
    the search's own ranks 0 and 1 and every outlier is the search's own
    candidate -- the plan applies the rules instead of replaying a recording."""
    pm = _plan()
    ws = I._worldseed()
    towns, tsrc = pm.declared_town_centres()
    sites, ssrc = pm.declared_outlier_sites()
    assert not towns and not sites, \
        f"a standing declaration pins {sorted(towns) + sorted(sites)}"
    got = ws.human_sites("Pirate68")
    assert {k: tuple(v["xz"]) for k, v in got.items()} == {
        "harbour-city-ulfsgard": (1050, 720),
        "harbour-village-sudrvik": (1190, 444)}, \
        {k: v["xz"] for k, v in got.items()}
    cands = [_candidate(-262.0, 1998.0, "westisle", relief=18.5, meadows=1.0),
             _candidate(496.0, 904.0, "mainland", relief=17.8, meadows=0.998),
             _candidate(-906.0, 1846.0, "westisle", relief=13.3, meadows=1.0)]
    picks = pm.select_towns(cands, "Pirate68", towns, tsrc)
    got_xz = [(p["site"].x, p["site"].z) for p in picks]
    assert got_xz == [(-262.0, 1998.0), (496.0, 904.0)], \
        (f"with nothing declared the towns are the search's ranks 0 and 1, "
         f"got {got_xz}")
    assert all(p["declared_xz"] is None for p in picks), \
        "a town nobody declared was reported as declared"
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
                                _verdicts({ULF_LIGHT}), context=_CTX)
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
    """AND WHERE THE SITE IS DECLARED (an operator's `--expect-sites`), IT DOES
    NOT.  A declared site is published with that body's piece count and sha256
    and may be STANDING, so substituting it is a plan that stops describing the
    ground -- the refusal is the deliverable and the repair is to the
    blueprint."""
    pm = _plan()
    picker = _enough_candidates(pm, fields=dict(DERIVED_FIELD))
    anchors, source = pm.declared_outlier_sites(
        pm.parse_site_declaration(["lh-west=-311,2570,trunk-secondary"]))
    placed = pm.select_outliers(picker, "Fresh42", anchors, source,
                                _verdicts({ULF_LIGHT}), context=_CTX)
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
                                _verdicts({ULF_LIGHT, DRAKE_LIGHT}),
                                context=_CTX)
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

    def unscoped(picker, seed, anchors, source, body_ok=None, **kw):
        placed = saved(picker, seed, anchors, source, body_ok, **kw)
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
    2026-09-22 -- records filtered to the sites this plan builds, and NO
    membership test in either direction.  It judged the solver's records and
    was silent about everything else: a record nothing builds, and (since the
    declaration moved to `worldseed.HUMAN_SITES`) a declared site that was
    never published."""
    saved = globals()["audit"]

    def towns_only(doc, where):
        pm = _plan()
        out: list[Finding] = []
        if not doc.get("seed"):
            return out
        ids = {t["id"] for t in pm.TOWNS} | {o["id"] for o in pm.OUTLIERS}
        for r in (doc.get("sites") or []):
            if r.get("id") in ids:
                out.append(Finding(
                    check="published_sites_are_the_declared_set", ok=True,
                    advisory=True,
                    message=f"{r['id']} is published", numbers={}))
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


def _dedupe_fixture():
    import numpy as np
    S = _plan().S
    fields = {}
    for name, cx, cz, half in (
            ("trunk-primary", 0.0, 0.0, 512.0),
            ("trunk-secondary", 200.0, 0.0, 512.0),
            ("north-search-extra", 100.0, 100.0, 256.0)):
        fields[name] = S.T.Field(
            name, cx, cz, half, half, 2,
            np.full((2, 2), 41.0, dtype=np.float32),
            np.full((2, 2), S.T.BIOME_CODE["Meadows"], dtype=np.uint8))
    return types.SimpleNamespace(fields=fields)


def _case_unsearched_coverage_does_not_erase_a_candidate():
    S = _plan().S
    candidate = _candidate(100.0, 100.0, "trunk-primary")
    got = S.dedupe(_dedupe_fixture(), [candidate])
    positions = [(s.patch, s.x, s.z) for s in got]
    return Finding(
        check="unsearched_coverage_preserves_candidate",
        ok=positions == [("trunk-primary", 100.0, 100.0)],
        message="extra coverage cannot invalidate a district already measured by its search",
        numbers={"retained": positions})


def _case_only_actual_duplicate_candidates_compete():
    S = _plan().S
    candidates = [_candidate(100.0, 100.0, "trunk-primary"),
                  _candidate(102.0, 100.0, "trunk-secondary"),
                  _candidate(300.0, 100.0, "trunk-primary")]
    got = S.dedupe(_dedupe_fixture(), candidates)
    positions = sorted((s.patch, s.x, s.z) for s in got)
    expected = sorted([("trunk-secondary", 102.0, 100.0),
                       ("trunk-primary", 300.0, 100.0)])
    return Finding(
        check="actual_duplicate_candidates_compete",
        ok=positions == expected,
        message="nearby duplicates choose the nearer measured patch; a distinct district survives",
        numbers={"retained": positions, "expected": expected})


def _neuter_dedupe_to_global_field_ownership():
    import math
    S = _plan().S
    saved = S.dedupe

    def dedupe(picker, candidates, within_m=40.0):
        out = []
        for site in candidates:
            owner = S.T.field_for(picker.fields, site.x, site.z)
            if owner is not None and owner.id != site.patch:
                continue
            if any(math.hypot(site.x - other.x, site.z - other.z)
                   < within_m for other in out):
                continue
            out.append(site)
        return out

    S.dedupe = dedupe

    def undo():
        S.dedupe = saved
    return undo


def _case_street_geometry_matches_its_walk_measurement():
    import math
    import numpy as np
    pm = _plan()
    field = pm.T.Field(
        "trunk-primary", 0.0, 0.0, 300.0, 1.0, 600,
        np.full((600, 600), 40.0, dtype=np.float32),
        np.full((600, 600), pm.T.BIOME_CODE["Meadows"], dtype=np.uint8))
    planner = pm.LY.TownPlanner(
        types.SimpleNamespace(fields={field.id: field}), audit_rows=[])
    doc = planner.plan(_candidate(0.0, 0.0, field.id), "constructed", [],
                       street_len_m=160.0)
    measurements = []
    for street in doc["streets"]:
        bearing = math.radians(street["bearing_deg"])
        nodes = street["nodes"]
        lateral = max(abs(-math.sin(bearing) * x + math.cos(bearing) * z)
                      for x, z in nodes)
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                     for a, b in zip(nodes, nodes[1:]))
        measurements.append({"street": street["name"], "lateral_m": lateral,
                             "actual_length_m": length,
                             "measured_length_m": street["profile"]["length_m"]})
    return Finding(
        check="street_geometry_matches_walk_measurement",
        ok=all(m["lateral_m"] < 1e-9
               and abs(m["actual_length_m"] - m["measured_length_m"]) <= 0.050000001
               for m in measurements),
        message="street nodes must follow their advertised bearing and measured walking distance",
        numbers={"streets": measurements})


def _neuter_cross_street_arguments():
    module = _plan().LY
    saved = module.street_through

    def swapped(field, cx, cz, *args, **kwargs):
        if "bearing_deg" in kwargs and "length_m" in kwargs:
            kwargs["bearing_deg"], kwargs["length_m"] = (
                kwargs["length_m"], kwargs["bearing_deg"])
        return saved(field, cx, cz, *args, **kwargs)

    module.street_through = swapped

    def undo():
        module.street_through = saved
    return undo


def _frontage_world():
    """Construct rough ground with one remote legal pad, not a canned bill."""
    import numpy as np
    pm = _plan()
    n, half = 400, 200.0
    axis = -half + 0.5 + np.arange(n)
    x, z = np.meshgrid(axis, axis)
    h = np.where(np.indices((n, n)).sum(axis=0) % 2, 20.0, 60.0)
    # Two narrow level roads, the square, and a flat plot 80 m from it.
    level = ((np.abs(z) <= 3) | (np.abs(x) <= 3)
             | ((np.abs(x) <= 12) & (np.abs(z) <= 12))
             | ((x >= -90) & (x <= -70) & (z >= 4) & (z <= 24)))
    h[level] = 40.0
    field = pm.T.Field(
        "frontage-ground", 0.0, 0.0, half, 1.0, n, h.astype(np.float32),
        np.full((n, n), pm.T.BIOME_CODE["Meadows"], dtype=np.uint8))
    loc = pm.clearance.Locations(
        xz=np.array([[10000.0, 10000.0]], dtype=np.float32),
        names=["far marker"], prefabs=["Runestone"],
        reach=np.array([0.0]), protected=np.array([False]),
        standoff=np.array([10.0]), spawn=(0.0, 0.0), by_type={})
    picker = object.__new__(pm.S.Picker)
    picker.fields, picker.loc, picker.installations = {field.id: field}, loc, []
    row = {"name": "constructed-house", "sha256": "constructed",
           "footprint_x_m": 8.0, "footprint_z_m": 8.0, "pieces": 12,
           "flat_pad_verdict": "grounds", "floating_fraction": 0.0,
           "stations": 0, "beds": 2}
    return pm, field, picker, row


def _case_frontage_fallback_finds_ground_the_sweeps_miss():
    pm, field, picker, row = _frontage_world()
    ly = pm.LY
    planner = ly.TownPlanner(picker, audit_rows=[row])
    # Exercise the actual old anchors and sweeps first: none reaches the
    # level pad at x=-80. Every refusal is measured by the real terrain bill.
    for bearing, length in ((0.0, 300.0), (90.0, 180.0)):
        street = ly.Street("preferred", bearing,
                          ly.street_through(field, 0, 0, bearing, length), {})
        anchor = length / 2 + 8 + ly.MIN_BUILDING_GAP_M + 3
        for side in (1, -1):
            assert planner.place([], field, "probe", "house", row, street,
                                 side, anchor) is None
    doc = planner.plan(_candidate(0, 0, field.id), "constructed",
                       [("house", row["name"], 1)], street_len_m=300.0)
    assert doc["totals"]["buildings_requested"] == 1
    assert doc["totals"]["buildings_unplaced"] == 0, doc["unplaced"]
    building, = doc["buildings"]
    assert -90 <= building["x"] <= -70 and 4 <= building["z"] <= 24, building
    assert building["pad"]["clamp_ok"] and not building["pad"]["wet_samples"]
    assert field.contains(building["x"], building["z"],
                          max(building["pad"]["width_m"],
                              building["pad"]["depth_m"]))
    return []


def _case_frontage_fallback_keeps_installation_refusal():
    pm, field, picker, row = _frontage_world()
    # The only level pad is now occupied. The real Picker installation
    # stand-off must still refuse it, even in the exhaustive fallback pass.
    picker.installations = [{"x": -80.0, "z": 14.0, "radius_m": 20.0}]
    planner = pm.LY.TownPlanner(picker, audit_rows=[row])
    doc = planner.plan(_candidate(0, 0, field.id), "constructed",
                       [("house", row["name"], 1)], street_len_m=300.0)
    assert doc["buildings"] == [], doc["buildings"]
    assert doc["totals"]["buildings_requested"] == 1
    assert doc["totals"]["buildings_unplaced"] == 1
    miss, = doc["unplaced"]
    assert miss["sampled_frontages_exhausted"] and miss["positions_evaluated"] > 0
    return []


def _neuter_frontage_fallback():
    module = _plan().LY
    saved = module.TownPlanner.frontage_positions

    def preferred_only(street, along_m=None):
        return () if along_m is None else saved(street, along_m)

    module.TownPlanner.frontage_positions = staticmethod(preferred_only)

    def undo():
        module.TownPlanner.frontage_positions = staticmethod(saved)
    return undo


def _neuter_frontage_installation_gate():
    module = _plan().S
    saved = module.Picker.installation_conflict
    module.Picker.installation_conflict = lambda self, x, z, half_diag_m: None

    def undo():
        module.Picker.installation_conflict = saved
    return undo


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


# THE CONTEXT A ROLE READS besides the candidate: the first town's centre.
_CTX = {"town": (1000.0, -2000.0)}


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
        placed = pm.select_outliers(picker, "Fresh42", {}, "", context=_CTX)
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
    ranks = sorted(r["search_rank"] for r in placed
                   if r["kind"] == "watchtower")
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
        placed = pm.select_outliers(picker, "Fresh42", {}, "", context=_CTX)
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
    """AN OPERATOR'S DECLARATION BINDS FOR THE SOLVE IT IS MADE FOR.  A site
    declared with `--expect-sites` is taken exactly where it is declared, its
    window resolved from the coordinate, never re-searched -- and the sites
    nobody declared stay the search's."""
    pm = _plan()
    # Candidates on the field's own window, so a selection that ignored the
    # declaration would succeed and move the declared sites -- which is what
    # the neuter does and what this case must catch.
    picker = _enough_candidates(pm, fields=dict(DERIVED_FIELD))
    anchors = pm.parse_site_declaration(
        ["wt-spawn=1180,-2044", "lh-west=-311,2570,trunk-secondary"])
    anchors, source = pm.declared_outlier_sites(anchors)
    assert "--expect-sites" in source, source
    placed = pm.select_outliers(picker, "Fresh42", anchors, source,
                                context=_CTX)
    got = {r["id"]: (r["x"], r["z"], r["patch"]) for r in placed}
    assert got["wt-spawn"][:2] == (1180.0, -2044.0), got["wt-spawn"]
    assert got["lh-west"] == (-311.0, 2570.0, "trunk-secondary"), got["lh-west"]
    declared = {r["id"] for r in placed if r["centre_declared"]}
    assert declared == {"wt-spawn", "lh-west"}, \
        f"only the two declared sites are declared, got {sorted(declared)}"
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
        placed = pm.select_outliers(picker, "Fresh42", anchors, "a test",
                                    context=_CTX)
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

    def literal(picker, seed, anchors, source, **kw):
        out = []
        for i, o in enumerate(pm.OUTLIERS):
            rec = dict(o)
            rec.update({"x": 110.0 + 17.0 * i, "z": -104.0 + 31.0 * i,
                        "patch": "mainland",
                        "why": "MEASURED: a literal record", 
                        "centre_declared": True, "search_rank": None})
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

    def search_only(picker, seed, anchors, source, **kw):
        return saved(picker, seed, {}, "", **kw)

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


def _neuter_to_the_frozen_pair():
    """RESTORE THE DEFECT VERBATIM: `plan.ANCHORED_TOWNS['Pirate68']` -- the
    two places the search had returned on an earlier run, kept as if a person
    had chosen them."""
    pm = _plan()
    saved = pm.declared_town_centres
    frozen = {"stenvik": {"xz": (496, 904), "why": "frozen"},
              "vestvik": {"xz": (-906, 1846), "why": "frozen"}}
    pm.declared_town_centres = lambda declared=None: (frozen, "frozen")

    def undo():
        pm.declared_town_centres = saved
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
    placed = pm.select_outliers(picker, "Fresh42", {}, "", context=_CTX)
    # WHICHEVER TREEHOUSE NAME the roles bind it to: the 5.7 m candidate must
    # stand for none of them.
    tn = next((r for r in placed if r["kind"] == "treehouse"
               and (r["x"], r["z"]) == (near.x, near.z)), None)
    stands = tn is not None
    tn = tn or next(r for r in placed if r["id"] == "tree-near")
    refused = [c for r in placed if r["kind"] == "treehouse"
               for c in (r.get("claim_refused") or [])]
    return Finding(
        check="site_claim_solver", ok=stands,
        message=(f"a treehouse candidate ({near.x}, {near.z}) is 5.7 m from "
                 f"lh-west at ({lw.x}, {lw.z}) and "
                 + (f"STANDS in the plan as {tn['id']}" if stands else
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


# ---------------------------------------------------------------------------
# THE SITES A PERSON DECLARED (`worldseed.HUMAN_SITES`), AND THE RULE THAT
# EVERYTHING ELSE IS THE SOLVER'S.  CONSTRUCTED: a seed that is not Pirate68
# with its own declaration over `harbour_site._fixture`'s 400 m field -- sea in
# the north, land in the south -- so no case is a reading of the host.
# ---------------------------------------------------------------------------
_HARBOUR_SEED = "Testseed"


def _declare_harbours():
    ws = I._worldseed()
    saved = ws.HUMAN_SITES.get(_HARBOUR_SEED)
    ws.HUMAN_SITES[_HARBOUR_SEED] = {"harbour-village-x": dict(
        kind="harbour_village", xz=(200, 130), node="villX", road_to=(),
        why='the owner: "a village at 200, 130"')}

    def undo():
        if saved is None:
            ws.HUMAN_SITES.pop(_HARBOUR_SEED, None)
        else:
            ws.HUMAN_SITES[_HARBOUR_SEED] = saved
    return undo


class _HarbourPicker:
    """What `plan.declared_harbours` asks of a picker: the fields, the spawn
    and the generator-location test, the installation test."""

    def __init__(self, fields):
        self.fields = fields
        self.loc = types.SimpleNamespace(
            spawn=(200.0, 300.0),
            clear=lambda x, z, half_diag, protected_only=False: True)

    def installation_conflict(self, x, z, half_diag_m):
        return None


def _harbour_world():
    pm = _plan()
    fields = pm.HS._fixture()
    f = fields["trunk-primary"]
    picker = _HarbourPicker(fields)
    lab, comps = pm.T.land_components(f, min_area_m2=1.0)
    sz, sx = f.idx(*picker.loc.spawn)
    islands = {"trunk-primary": {"labels": lab, "components": comps,
                                 "spawn_label": int(lab[sz, sx]) or None}}
    return pm, picker, islands


def _district_unit(x: float, z: float) -> dict:
    return {"id": "wt-x", "x": x, "z": z, "yaw": 0.0, "pad_w": 16.0,
            "pad_d": 16.0, "foot_w": 16.0, "foot_d": 16.0, "target_y": 35.0}


def _case_a_declared_site_is_measured_and_claims_its_district_first():
    """THE OWNER'S REQUEST IS NOT THE NODE, AND THE DISTRICT IS NOT FREE
    GROUND.  A request under the sea is sited by its own module at the real
    landing, and the ground it reserves is claimed BEFORE any solver site, so a
    watchtower the search likes inside the harbour district is moved off it
    (and one far outside is not)."""
    pm, picker, islands = _harbour_world()
    undo = _declare_harbours()
    try:
        claims = pm.SiteClaims()
        got = pm.declared_harbours(picker, _HARBOUR_SEED, claims, islands)
    finally:
        undo()
    assert len(got) == 1, got
    m = got[0]
    assert m["requested_xz"] == [200.0, 130.0], m["requested_xz"]
    assert m["xz"] != m["requested_xz"], \
        "the node is the request: nothing was measured"
    assert m["island"]["same_landmass_as_spawn"] is True, m["island"]
    assert len(claims.claims) == 1 and \
        claims.claims[0]["kind"] == "harbour_village", claims.claims
    inside = claims.conflicts(pm.SiteClaims.claim(
        "wt-x", "watchtower", [_district_unit(200.0, 200.0)], 35.0))
    outside = claims.conflicts(pm.SiteClaims.claim(
        "wt-x", "watchtower", [_district_unit(350.0, 370.0)], 35.0))
    assert inside, "a solver site inside the declared district was accepted"
    assert not outside, f"a site 200 m from the district was refused: {outside}"
    return []


def _neuter_the_declared_district_is_not_claimed():
    """RESTORE THE DEFECT: the declared site is measured and published and its
    ground is not reserved, so the solver plants a site inside the harbour."""
    pm = _plan()
    saved = pm.SiteClaims.add
    pm.SiteClaims.add = lambda self, claim: None

    def undo():
        pm.SiteClaims.add = saved
    return undo


def _case_a_seed_without_declarations_is_unchanged():
    """A SEED NOBODY DECLARED ANYTHING FOR IS SOLVED BY THE RULES ALONE, TO THE
    BYTE: no declared record, no claim, no `declared_sites` key in the
    published site set -- while a seed that declares one gets the key."""
    pm, picker, islands = _harbour_world()
    claims = pm.SiteClaims()
    assert pm.declared_harbours(picker, "Fresh42", claims, islands) == []
    assert claims.claims == [], "a seed with no declaration claimed ground"
    with tempfile.TemporaryDirectory() as td:
        plan = pm._write_fixture_plan(Path(td) / "field.yaml")
    assert "declared_sites" not in pm.sites_yaml(plan), \
        "a seed with no declaration published a declared_sites key"
    undo = _declare_harbours()
    try:
        rec = pm.declared_harbours(picker, _HARBOUR_SEED, pm.SiteClaims(),
                                   islands)
    finally:
        undo()
    text = pm.sites_yaml(dict(plan, declared_sites=rec))
    assert "declared_sites" in text and "harbour-village-x" in text, text[:300]
    return []


def _neuter_sites_yaml_always_publishes_declared():
    pm = _plan()
    saved = pm.sites_yaml

    def always(plan):
        return saved(plan) + "declared_sites: []\n"

    pm.sites_yaml = always

    def undo():
        pm.sites_yaml = saved
    return undo


def _spec():
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import spec as specmod                                   # noqa: PLC0415
    return specmod


def _solved_fixture() -> dict:
    def rec(sid, x, z, patch="trunk-primary", prio="trunk", r=16.1,
            roaded=True):
        why = ("road_priority is none: portal-only, by design" if prio == "none"
               else "the router snap stands in the largest strong component")
        return {"type": "watchtower", "xz": (x, z), "island": "spawn",
                "patch": patch, "measurement_patch": patch,
                "pad_radius_m": r, "priority": prio,
                "tag": f"u-{sid}", "pad_y": 70.25, "solver_id": sid,
                "road": {"class": prio, "roaded": roaded,
                         "window": patch, "topology_window": patch,
                         "reason": ("reached" if roaded
                                    else "declared_portal_only"),
                         "why": why, "evidence": {"island": "spawn"}}}
    return {"stenvik": rec("stenvik", 700.0, -300.0, r=100.0),
            "wtspawn": rec("wt-spawn", 1432.0, 1546.0),
            "sudrberg": rec("sudrberg-keep", 1542.0, 802.0, prio="none",
                            roaded=False)}


def _case_undeclared_sites_are_the_solvers_to_the_coordinate():
    """NOTHING IS LAYERED OVER THE SOLVER.  The default pair's sites reach the
    road network at the coordinate, the radius and the datum `sites.yaml`
    publishes -- not at a frozen answer from an earlier run -- and a fresh seed
    gets exactly the same.  The ONE thing between `sites.yaml` and the node
    table is the solver's own road verdict: a site no road reaches is
    portal-only, with its numbers, on every seed alike."""
    specmod = _spec()
    solved = _solved_fixture()
    sites, prov = specmod.adopt_sites(solved)
    for sid in ("stenvik", "wtspawn"):
        for field in ("xz", "pad_radius_m", "pad_y"):
            assert sites[sid][field] == solved[sid][field], \
                (f"{sid}.{field} is {sites[sid][field]!r}, the solver "
                 f"published {solved[sid][field]!r}: something is layered "
                 f"over the solver")
    assert "sudrberg" not in sites and "sudrberg" in prov["portal_only"], \
        "a site the solver published as not roaded must be portal-only"
    assert "road_priority is none" in prov["portal_only"]["sudrberg"]["why"], \
        "the portal-only record carries the solver's own reason"
    return []


def _neuter_the_solver_is_pinned_again():
    """RESTORE THE DEFECT VERBATIM: the 15-record `spec.PINNED_SITES` layer --
    last run's answer, frozen -- applied over the solver on the default pair."""
    specmod = _spec()
    saved = specmod.adopt_sites

    def pinned(solved=None):
        sites, prov = saved(solved)
        sites["stenvik"] = dict(sites["stenvik"], xz=(496.0, 904.0))
        sites["wtspawn"] = dict(sites["wtspawn"], xz=(110.0, -104.0),
                                pad_radius_m=12.0)
        return sites, prov

    specmod.adopt_sites = pinned

    def undo():
        specmod.adopt_sites = saved
    return undo


# ---------------------------------------------------------------------------
# A SITE IS WHERE A ROAD CAN GET TO (settlements/road_reach.py), AND A NAME IS
# WHAT THE SITE IS (plan.ROLES) -- the two things the regenerated Pirate68
# broke the moment its 15 frozen pins came out.  MEASURED 2026-10-07: 35
# segments, 7 UNROUTABLE.  `wt-spawn` (1432, 1546) stood in a 186-cell pocket
# of the router's own graph on the spawn landmass; `wt-north`, `wt-east`,
# `lh-east`, `lh-west`, `tree-west` and `tree-near` stood on a 62,618-cell
# island of it; and every leg typed for one seed's `wt-spawn` ran to this
# seed's accident, because the solver bound a NAME to whatever it ranked.
# CONSTRUCTED: a 400 m window of sea, one island with a walled block on it, and
# one islet 44 m off its shore; no field file, no dump.
# ---------------------------------------------------------------------------
def _road_reach_fixture(joined=("spawn",)):
    import numpy as np
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.append(str(roads))
    import field as fieldmod                                # noqa: PLC0415
    import geography as G                                   # noqa: PLC0415
    half, n = 200.0, 400
    ax = -half + 0.5 + np.arange(n, dtype=np.float64)
    X, Z = np.meshgrid(ax, ax)
    # SHORES THAT SLOPE.  A cliff shore would split the graph into land and
    # sea (a road can step off a cliff into water and cannot step back up), so
    # the sea would be the largest component; a real coast ramps, and so does
    # this one -- 12 % on the island, 20 % on the islet, sea at the plane.
    h = np.full((n, n), 28.0)
    d_main = np.hypot(X + 50.0, Z)
    main = d_main <= 100.0
    h[main] = 28.0 + 0.12 * (100.0 - d_main[main])
    h[main & (X >= 10.0) & (X <= 40.0) & (np.abs(Z) <= 15.0)] = 80.0
    d_islet = np.hypot(X - 130.0, Z)
    islet = d_islet <= 70.0
    h[islet] = np.minimum(40.0, 28.0 + 0.2 * (70.0 - d_islet[islet]))
    fld = fieldmod.Field("trunk-primary", 0.0, 0.0, half, 1.0, n,
                         h.astype(np.float32),
                         np.ones((n, n), dtype=np.uint8))
    m = n // 4
    sr = _plan().RR.SiteReach(
        {"trunk-primary": fld}, None, joined=set(joined),
        island_of=lambda patch: "spawn",
        graph_for=lambda f, c: G.RoadGraph(
            f, forbid=np.zeros((m, m), dtype=bool), cls=c))
    return sr, {"land": (-50.0, 60.0), "pocket": (25.0, 0.0),
                "islet": (130.0, 0.0)}


def _coverage_reach_fixture():
    sr, pts = _road_reach_fixture()
    # A search window measures the same terrain, but has no island role.
    sr.fields["survey-tile"] = sr.fields["trunk-primary"]
    sr.island_of = lambda pid: "spawn" if pid == "trunk-primary" else pid
    return sr, pts


def _case_a_survey_tile_preserves_measured_road_reach():
    sr, pts = _coverage_reach_fixture()
    baseline = sr.verdict("trunk-primary", *pts["land"], "trunk")
    survey = sr.verdict("survey-tile", *pts["land"], "trunk")
    return Finding(
        check="survey_preserves_road_reach",
        ok=(baseline["roaded"] and survey["roaded"]
            and survey["window"] == "survey-tile"
            and survey.get("topology_window") == "trunk-primary"),
        message=f"the same land is {baseline['reason']} from its trunk window "
                f"and {survey['reason']} from its survey measurement",
        where=pts["land"], numbers={"source_windows": 2})


def _case_survey_coverage_does_not_root_an_islet_or_pocket():
    sr, pts = _coverage_reach_fixture()
    verdicts = [sr.verdict("survey-tile", *pts[p], "trunk")
                for p in ("pocket", "islet")]
    # These are the real graph and land labels from the sloped-coast fixture.
    return Finding(
        check="survey_keeps_topology_refusals",
        ok=any(v["roaded"] for v in verdicts),
        message="survey measurements: " + ", ".join(
            f"{p} -> {v['reason']}" for p, v in
            zip(("pocket", "islet"), verdicts)),
        where=pts["pocket"],
        numbers={"roaded": sum(v["roaded"] for v in verdicts),
                 "reasons": [v["reason"] for v in verdicts]})


def _case_survey_coverage_keeps_approach_grade():
    sr, pts = _coverage_reach_fixture()
    cliff = sr.verdict("survey-tile", *pts["land"], "spur",
                       pad_y=60.0, pad_m=22.0)
    return Finding(
        check="survey_keeps_approach_grade", ok=cliff["roaded"],
        message=f"survey pad 60 m over the coast -> {cliff['reason']}",
        where=pts["land"], numbers={"pad_y": 60.0})


def _case_survey_coverage_without_a_root_is_not_a_road():
    sr, pts = _coverage_reach_fixture()
    # A rooted window exists, but not HERE. Shared window names or other
    # windows' components cannot provide a path at this coordinate.
    sr.fields["trunk-primary"] = _StubField(
        "trunk-primary", 1000.0, 1200.0, 1000.0, 1200.0)
    unknown = sr.verdict("survey-tile", *pts["land"], "trunk")
    declared = sr.verdict("survey-tile", *pts["land"], "none")
    return Finding(
        check="survey_requires_covering_root",
        ok=unknown["roaded"] or declared["roaded"],
        message=f"no covering root -> {unknown['reason']}; "
                f"declared none -> {declared['reason']}",
        where=pts["land"], numbers={"covering_rooted_windows": 0})


def _neuter_reach_to_the_measurement_window():
    """Restore the source-window island gate without weakening any graph."""
    mod = _plan().RR
    saved = mod.SiteReach.verdict

    def source_only(self, patch, x, z, priority, **kw):
        if priority != "none" and self.island_of(patch) not in self._joined():
            return {"class": priority, "window": patch, "xz": [x, z],
                    "roaded": False, "reason": "unrooted_window",
                    "why": "the source window ID is taken as its island",
                    "evidence": {}}
        return saved(self, patch, x, z, priority, **kw)

    mod.SiteReach.verdict = source_only

    def undo():
        mod.SiteReach.verdict = saved
    return undo


def _coverage_land_fixture():
    pm = _plan()
    sr, pts = _coverage_reach_fixture()
    f = sr.fields["trunk-primary"]
    terrain = pm.T.Field(f.id, f.cx, f.cz, f.half, f.step, f.n, f.h, f.biome)
    picker = types.SimpleNamespace(
        fields={"trunk-primary": terrain, "survey-tile": terrain},
        loc=types.SimpleNamespace(spawn=pts["land"]))
    islands = pm.island_membership(picker, patches=("trunk-primary",))
    return pm, picker, islands, pts


def _case_survey_id_is_not_a_separate_landmass():
    pm, picker, islands, pts = _coverage_land_fixture()
    got = pm.site_island(islands, picker, "survey-tile", *pts["pocket"])
    return Finding(
        check="survey_landmass_uses_components",
        ok=(got["same_landmass_as_spawn"] is True
            and got.get("topology_window") == "trunk-primary"
            and got.get("window") == "survey-tile"),
        message=f"survey pocket on spawn's land: {got['evidence']}",
        where=pts["pocket"], numbers={"labelled_windows": len(islands)})


def _case_survey_landmass_keeps_separate_and_unknown_distinct():
    pm, picker, islands, pts = _coverage_land_fixture()
    islet = pm.site_island(islands, picker, "survey-tile", *pts["islet"])
    # This window has labels, but no measured spawn component. The same
    # numeric label in a different window cannot establish connectivity.
    islands = {"survey-tile": {**islands["trunk-primary"], "spawn_label": None}}
    unknown = pm.site_island(islands, picker, "survey-tile", *pts["land"])
    return Finding(
        check="survey_landmass_distinguishes_unknown",
        ok=(islet["same_landmass_as_spawn"] is False
            and unknown["same_landmass_as_spawn"] is None
            and unknown.get("topology_window") is None),
        message=f"islet -> {islet['same_landmass_as_spawn']}; "
                f"no spawn label -> {unknown['same_landmass_as_spawn']}",
        where=pts["islet"], numbers={"unrooted_label_windows": 1})


def _neuter_landmass_to_the_measurement_window():
    """Restore the missing-source-label-map means separate-island defect."""
    pm = _plan()
    saved = pm.site_island

    def source_only(islands, picker, patch, x, z):
        if patch not in islands:
            return {"same_landmass_as_spawn": False,
                    "evidence": "survey ID taken as a separate landmass"}
        return saved(islands, picker, patch, x, z)

    pm.site_island = source_only

    def undo():
        pm.site_island = saved
    return undo


def _neuter_landmass_unknown_to_separate():
    pm = _plan()
    saved = pm.site_island

    def unknown_is_separate(*a, **kw):
        got = saved(*a, **kw)
        if got["same_landmass_as_spawn"] is None:
            got["same_landmass_as_spawn"] = False
        return got

    pm.site_island = unknown_is_separate

    def undo():
        pm.site_island = saved
    return undo


def _reach_finding(check: str, point: str, priority: str = "trunk",
                   joined=("spawn",)):
    sr, pts = _road_reach_fixture(joined)
    v = sr.verdict("trunk-primary", *pts[point], priority)
    ev = v["evidence"]
    return v, Finding(
        check=check, ok=v["roaded"],
        message=(f"the {point} at {pts[point]} is judged {v['reason']}: "
                 f"{v['why']}"),
        where=pts[point],
        numbers={"reason": v["reason"],
                 "piece_cells": ev.get("piece_cells"),
                 "network_cells": ev.get("network_cells"),
                 "land_cells": ev.get("land_cells"),
                 "trunk_land_cells": ev.get("trunk_land_cells")})


def _case_a_pocket_the_router_cannot_enter_is_portal_only():
    """`wt-spawn`'s state, constructed: a site ON the trunk landmass whose
    router snap stands in a pocket of the graph (a 40 m cliff all round).  A
    leg to it is `no route found`; the verdict is portal-only, with the
    pocket's size beside the network's."""
    v, finding = _reach_finding("road_reach_pocket", "pocket")
    assert v["reason"] == "off_network", \
        f"a walled block on the trunk landmass was judged {v['reason']}"
    assert v["evidence"]["land_cells"] == v["evidence"]["trunk_land_cells"], \
        "the pocket stands on the trunk landmass; only the graph refuses it"
    assert v["evidence"]["piece_cells"] < v["evidence"]["network_cells"], v
    return finding


def _case_an_islet_across_the_water_is_portal_only():
    """`wt-north`'s state, constructed: an islet 44 m off the island's shore.
    The router's graph LETS a road cross 44 m of channel -- that is how the two
    halves of the main continent are one -- so the graph alone says
    reachable; the verdict is portal-only, because a road does not cross open
    water to another landmass: a dock, a ferry or a portal does."""
    v, finding = _reach_finding("road_reach_islet", "islet")
    assert v["reason"] == "off_landmass", \
        f"an islet across a channel was judged {v['reason']}"
    assert v["evidence"]["piece_cells"] == v["evidence"]["network_cells"], \
        "the graph itself reaches the islet; only the landmass rule refuses it"
    return finding


def _case_a_pad_the_road_cannot_arrive_on_is_portal_only():
    """`lh-south`'s state, constructed: a site whose snap the graph reaches,
    on a coast whose land is 20+ m from the pad datum at the stand-off ring --
    a 12 % spur cannot close that over the run.  The verdict is `approach_cliff`
    with the numbers; the same site with a datum its ring can meet is roaded
    (the survivor, in the same case)."""
    sr, pts = _road_reach_fixture()
    x, z = pts["land"]
    ok = sr.verdict("trunk-primary", x, z, "spur", pad_y=33.0, pad_m=22.0)
    cliff = sr.verdict("trunk-primary", x, z, "spur", pad_y=60.0, pad_m=22.0)
    assert ok["roaded"], f"a pad its ring can meet was refused: {ok['why']}"
    assert cliff["reason"] == "approach_cliff", \
        f"a pad 20+ m over its ring was judged {cliff['reason']}"
    ap = cliff["evidence"]["approach"]
    return Finding(
        check="road_reach_approach", ok=cliff["roaded"],
        message=f"the pad at {pts['land']} is judged {cliff['reason']}: "
                f"{cliff['why']}",
        where=pts["land"],
        numbers={"best_gap_m": ap["best_gap_m"], "allowed_m": ap["allowed_m"],
                 "land_samples": ap["land_samples"]})


def _neuter_the_approach_is_not_asked():
    """RESTORE THE DEFECT: `_APPROACH_MODE = "snap"` -- the router's snap
    alone decides whether a site is roaded."""
    mod = _plan().RR
    saved = mod._APPROACH_MODE
    mod._APPROACH_MODE = "snap"

    def undo():
        mod._APPROACH_MODE = saved
    return undo


def _case_a_site_on_the_trunk_landmass_is_roaded():
    """THE SURVIVOR: ground on the trunk landmass, in the network's own
    component, is roaded -- a check that refuses everything blocks a world
    that is fine."""
    sr, pts = _road_reach_fixture()
    v = sr.verdict("trunk-primary", *pts["land"], "trunk")
    assert v["roaded"] and v["reason"] == "reached", v["why"]
    assert v["evidence"]["piece_cells"] == v["evidence"]["network_cells"], v
    return []


def _case_none_and_unrooted_are_portal_only():
    """The two declarations that need no graph: `road_priority: none` is
    "portal-only, by design", and a window no crossing roots on this seed is
    walkable from nowhere a walk from the portal hall starts."""
    sr, pts = _road_reach_fixture()
    declared = sr.verdict("trunk-primary", *pts["land"], "none")
    unrooted, _f = _reach_finding("road_reach_unrooted", "land", joined=())
    assert declared["reason"] == "declared_portal_only", declared
    roaded = [v["reason"] for v in (declared, unrooted) if v["roaded"]]
    return Finding(
        check="road_reach_declared", ok=bool(roaded),
        message=(f"road_priority none -> {declared['reason']}; a window with "
                 f"no crossing -> {unrooted['reason']}"
                 + (f"; ROADED anyway: {roaded}" if roaded else "")),
        where=pts["land"], numbers={"roaded_anyway": len(roaded)})


def _neuter_the_road_is_taken_on_the_declaration():
    """RESTORE THE DEFECT: `_MODE = "declared"` -- a declared road class IS a
    road, whatever the router's graph says."""
    mod = _plan().RR
    saved = mod._MODE
    mod._MODE = "declared"

    def undo():
        mod._MODE = saved
    return undo


def _neuter_the_landmass_is_not_asked():
    """RESTORE ONE HALF OF THE DEFECT: the graph reaches the islet, and
    nothing asks which landmass it is on."""
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.append(str(roads))
    import geography as G                                   # noqa: PLC0415
    saved = G.RoadGraph.at

    def at(self, xs, zs):
        out = saved(self, xs, zs)
        out["on_trunk_landmass"] = out["reached"]
        return out

    G.RoadGraph.at = at

    def undo():
        G.RoadGraph.at = saved
    return undo


def _neuter_the_road_refuses_everything():
    """THE EMPTIED INSTRUMENT: a verdict that says portal-only for every site
    -- the survivor must come out the other way."""
    mod = _plan().RR
    saved = mod.SiteReach.verdict

    def verdict(self, patch, x, z, priority):
        return {"class": priority, "window": patch, "xz": [x, z],
                "roaded": False, "reason": "off_network", "why": "nothing",
                "evidence": {}}

    mod.SiteReach.verdict = verdict

    def undo():
        mod.SiteReach.verdict = saved
    return undo


def _reach_stub(unreachable: set):
    """A road verdict CONSTRUCTED for the solver's cases: a candidate at one
    of these coordinates is in a pocket."""
    def reach(rec, s):
        bad = (round(s.x), round(s.z)) in unreachable
        return {"class": rec["road_priority"], "window": s.patch,
                "xz": [s.x, s.z], "roaded": not bad,
                "reason": "off_network" if bad else "reached",
                "why": ("a constructed 186-cell pocket" if bad
                        else "constructed: in the network"),
                "evidence": {"piece_cells": 186 if bad else 269270,
                             "network_cells": 269270}}
    return reach


def _towers_with_one_extra(pm, extra_at=(5000.0, 5000.0)):
    """`_enough_candidates` gives exactly as many candidates as names; one
    more, at the end of the search, so a name has somewhere to go when the
    best candidate is refused."""
    picker = _enough_candidates(pm)
    picker.per_kind["watchtower"].append(
        _kind_site("watchtower", extra_at[0], extra_at[1]))
    return picker


def _case_the_solver_passes_over_what_no_road_reaches():
    """THE SOLVER, NOT THE RULE: the search's FIRST watchtower candidate is in
    a pocket no road enters.  No site declared roaded may stand on it; the
    move is the search's next candidate and the refusal is on the record with
    its numbers (`reach_refused`)."""
    pm = _plan()
    picker = _towers_with_one_extra(pm)
    pocket = picker.per_kind["watchtower"][0]
    placed = pm.select_outliers(
        picker, "Fresh42", {}, "", context=_CTX,
        reach=_reach_stub({(round(pocket.x), round(pocket.z))}))
    on = [r for r in placed if (r["x"], r["z"]) == (pocket.x, pocket.z)]
    passed = sum(r.get("reach_refused_count", 0) for r in placed
                 if r["kind"] == "watchtower")
    assert passed > 0 or on, \
        "the pocket ranks first, so some tower must have passed it over"
    return Finding(
        check="solver_passes_over_what_no_road_reaches", ok=bool(on),
        message=(f"the first watchtower candidate ({pocket.x}, {pocket.z}) is "
                 f"in a 186-cell pocket and "
                 + (f"STANDS in the plan as {on[0]['id']}" if on else
                    f"was passed over by {passed} name(s) "
                    f"(reach_refused carries the numbers)")),
        where=(pocket.x, pocket.z),
        numbers={"passed_over": passed, "pocket_cells": 186,
                 "network_cells": 269270})


def _case_no_reachable_candidate_leaves_the_site_portal_only():
    """A BUILDING IS NEVER DELETED: when no watchtower candidate is reachable
    at all, the six names still stand, on the best candidates there are, each
    published `roaded: false` with the numbers -- not an exception, and not a
    road promised to ground none reaches."""
    pm = _plan()
    picker = _enough_candidates(pm)
    every = {(round(c.x), round(c.z)) for c in picker.per_kind["watchtower"]}
    placed = pm.select_outliers(picker, "Fresh42", {}, "", context=_CTX,
                                reach=_reach_stub(every))
    towers = [r for r in placed if r["kind"] == "watchtower"]
    assert len(towers) == 6, f"{len(towers)} watchtowers stand"
    for r in towers:
        road = r.get("road")
        assert road is not None and road["roaded"] is False, \
            f"{r['id']} carries road={road!r}"
        assert "PORTAL-ONLY" in r["why"], r["why"][-160:]
    return []


def _neuter_the_solver_ignores_the_road():
    """RESTORE THE DEFECT VERBATIM: `select_outliers` as it stood -- ranking
    candidates by what a tower needs and never asking whether a road can get
    there."""
    pm = _plan()
    saved = pm.select_outliers

    def blind(picker, seed, anchors, source, *a, **kw):
        kw.pop("reach", None)
        return saved(picker, seed, anchors, source, *a, **kw)

    pm.select_outliers = blind

    def undo():
        pm.select_outliers = saved
    return undo


def _watchtowers_by_rank():
    """Six watchtower candidates whose SEARCH RANK is the reverse of every
    role: rank 0 is the easternmost and the farthest, rank 5 the one on the
    temple's doorstep."""
    pm = _plan()
    S = pm.S

    def tower(x, z, rise, dist):
        t = _kind_site("watchtower", x, z)
        return S.Site(**{**t.__dict__, "rise_m": rise, "dist_spawn_m": dist})
    ranked = [("east", tower(1800.0, 200.0, 24.0, 1800.0)),
              ("north", tower(100.0, 1500.0, 22.0, 1500.0)),
              ("peak", tower(900.0, 100.0, 90.0, 800.0)),
              ("town", tower(500.0, 500.0, 30.0, 700.0)),
              ("south", tower(0.0, -900.0, 25.0, 900.0)),
              ("spawn", tower(0.0, -300.0, 20.0, 100.0))]
    picker = _enough_candidates(pm)
    picker.per_kind["watchtower"] = [t for _n, t in ranked]
    return pm, picker, {n: (t.x, t.z) for n, t in ranked}


def _case_names_are_bound_by_what_a_site_is_not_by_rank():
    """`wt-spawn` is the tower nearest the StartTemple, `wt-peak` the most
    prominent, `wt-north` the northernmost -- WHATEVER THE SEARCH RANKED.
    The first town stands at (500, 480), so `wt-town` is the one beside it."""
    pm, picker, at = _watchtowers_by_rank()
    placed = pm.select_outliers(picker, "Fresh42", {}, "",
                                context={"town": (500.0, 480.0)})
    got = {r["id"]: (r["x"], r["z"]) for r in placed
           if r["kind"] == "watchtower"}
    want = {"wt-spawn": at["spawn"], "wt-south": at["south"],
            "wt-town": at["town"], "wt-peak": at["peak"],
            "wt-north": at["north"], "wt-east": at["east"]}
    wrong = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    return Finding(
        check="names_are_what_a_site_is", ok=bool(wrong),
        message=(f"{len(want) - len(wrong)} of {len(want)} watchtower names "
                 f"stand on what they say"
                 + (f"; BOUND BY RANK: "
                    + ", ".join(f"{k} at {g} not {w}"
                                for k, (g, w) in sorted(wrong.items()))
                    if wrong else "")),
        where=next(iter(wrong.values()))[0] if wrong else None,
        numbers={"bound_by_rank": len(wrong), "names": len(want)})


def _neuter_names_are_bound_by_search_rank():
    """RESTORE THE DEFECT VERBATIM: each name takes the next candidate the
    search ranked."""
    pm = _plan()
    saved = pm._ROLE_MODE
    pm._ROLE_MODE = "rank"

    def undo():
        pm._ROLE_MODE = saved
    return undo


def _case_a_name_belongs_on_its_window():
    """`lh-west` hangs from a west-isle node, so it stands on the second trunk
    landmass even though the search ranked a primary-window lighthouse above
    every candidate there."""
    pm = _plan()
    picker = _enough_candidates(pm)
    picker.fields = {
        "trunk-primary": _StubField("trunk-primary", -9000.0, 9000.0,
                                    -9000.0, 9000.0),
        "trunk-secondary": _StubField("trunk-secondary", 4000.0, 9000.0,
                                      4000.0, 9000.0)}
    picker.per_kind["lighthouse"].append(
        _kind_site("lighthouse", 5000.0, 5000.0, "trunk-secondary"))
    placed = pm.select_outliers(picker, "Fresh42", {}, "", context=_CTX)
    lw = next(r for r in placed if r["id"] == "lh-west")
    return Finding(
        check="a_name_belongs_on_its_window",
        ok=lw["patch"] != "trunk-secondary",
        message=(f"lh-west stands at ({lw['x']}, {lw['z']}) on {lw['patch']}; "
                 f"its window is the second trunk landmass"),
        where=(lw["x"], lw["z"]),
        numbers={"search_rank": lw["search_rank"],
                 "window_requested": lw.get("window")})


def _case_a_portal_only_site_is_no_node_and_no_edge_names_it():
    """`roads/spec.py`: an edge whose end the road verdict ruled out is
    DROPPED, with the verdict's own numbers in `NO_ROAD` -- never routed into
    an UNROUTABLE -- and an end absent for NO recorded reason is still
    refused."""
    specmod = _spec()
    nodes = {"stathub": {}, "wtnorth": {}}
    portal = {"wtpeak": {"why": "(1856, 146) stands on a landmass of 25886 "
                                "cells; the trunk landmass holds 178613"}}
    edges = [{"id": "S7-stathub-wtpeak", "a": "stathub", "b": "wtpeak"},
             {"id": "S8-stathub-wtnorth", "a": "stathub", "b": "wtnorth"}]
    no_road: dict = {}
    dropped = specmod.drop_edges_without_nodes(
        edges, nodes,
        lambda n: (f"{n} is portal-only: {portal[n]['why']}"
                   if n in portal else None), {}, no_road)
    assert [e["id"] for e in dropped] == ["S7-stathub-wtpeak"], dropped
    assert [e["id"] for e in edges] == ["S8-stathub-wtnorth"], edges
    assert "25886" in no_road["S7-stathub-wtpeak"], no_road
    return []


def _neuter_the_edge_is_kept_for_the_router_to_fail_on():
    """RESTORE THE DEFECT: the edge stays in `EDGES` and the router discovers,
    leg by leg, that nothing is there."""
    specmod = _spec()
    saved = specmod.drop_edges_without_nodes

    def keep(edges, nodes, why_of, foreign, no_road):
        return []

    specmod.drop_edges_without_nodes = keep

    def undo():
        specmod.drop_edges_without_nodes = saved
    return undo


def _case_an_edge_to_a_node_absent_for_no_reason_is_refused():
    """The refusal that pre-dates all of this and is KEPT: a terminus that
    vanishes with nothing recorded is a road to nowhere."""
    specmod = _spec()
    try:
        specmod.drop_edges_without_nodes(
            [{"id": "S7-stathub-ghost", "a": "stathub", "b": "ghost"}],
            {"stathub": {}}, lambda n: None, {}, {})
    except SystemExit as exc:
        return Finding(
            check="edge_to_a_node_absent_for_no_reason", ok=False,
            message=f"refused: {str(exc)[:120]}", numbers={"edges": 1})
    return Finding(
        check="edge_to_a_node_absent_for_no_reason", ok=True,
        message="NOT REFUSED: an edge to a node nothing explains was dropped "
                "quietly", numbers={"edges": 1})


def _neuter_the_missing_node_vanishes_quietly():
    specmod = _spec()
    saved = specmod.drop_edges_without_nodes

    def quiet(edges, nodes, why_of, foreign, no_road):
        gone = [e for e in edges
                if any(n not in nodes for n in (e["a"], e["b"]))]
        for e in gone:
            edges.remove(e)
        return gone

    specmod.drop_edges_without_nodes = quiet

    def undo():
        specmod.drop_edges_without_nodes = saved
    return undo


register(Section(
    name="townsites",
    measures=("that a site's PLACE is a reading of the seed being solved -- the "
              "search's own top-ranked candidate of that kind, or a centre "
              "an operator DECLARED for that solve -- while what to build "
              "stays authored; "
              "that the only declared sites are the ones a person chose "
              "(worldseed.HUMAN_SITES), measured by their own module and held "
              "to the artefact RoadNet reads; "
              "that a solve which cannot put a declared site where it was "
              "declared, or cannot find ground for an undeclared one, STOPS, "
              "naming the seed, the declaration and the candidates it did find; "
              "and that every pad the emitter writes is BILLED on the surface "
              "the engine levels, over the samples the writer stamps; and "
              "that no site -- of ANY kind, town building or outlier -- is "
              "placed on ground another write already claims"),
    instrument=("settlements/plan.py::select_towns, ::select_outliers, "
                "::declared_town_centres, ::declared_outlier_sites, "
                "::resolve_patch, ::declared_harbours and "
                "settlements/harbour_site.py::measure and "
                "settlements/sites.py::trunk_patches and ::dedupe, "
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
                "run over, and the records of settlements/sites.yaml -- "
                "`sites` and `declared_sites`, the latter against "
                "worldseed.HUMAN_SITES; "
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
        Case("default_seed_declares_two_sites_and_pins_nothing", False,
             _case_the_default_seed_declares_two_sites_and_pins_nothing,
             prior_state=("settlements/plan.py carried ANCHORED_TOWNS "
                          "(stenvik (496, 904), vestvik (-906, 1846)) and "
                          "ANCHORED_OUTLIERS (14 sites) and roads/spec.py "
                          "carried PINNED_SITES (15 records): the places the "
                          "same search had returned on an earlier run, "
                          "frozen -- not one of them a person's decision"),
             neuter=_neuter_to_the_frozen_pair,
             neuters="plan.declared_town_centres answering the old "
                     "ANCHORED_TOWNS pair"),
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
        Case("unsearched_coverage_preserves_a_measured_candidate", False,
             _case_unsearched_coverage_does_not_erase_a_candidate,
             prior_state="adding north coverage erased all five real town candidates",
             neuter=_neuter_dedupe_to_global_field_ownership,
             neuters="dedupe assigns candidate ownership to every field, including unsearched tiles"),
        Case("only_actual_duplicate_candidates_compete", False,
             _case_only_actual_duplicate_candidates_compete,
             prior_state="a nearer field could erase a district without offering a competing candidate",
             neuter=_neuter_dedupe_to_global_field_ownership,
             neuters="dedupe uses global field ownership instead of actual duplicate candidates"),
        Case("street_geometry_matches_walk_measurement", False,
             _case_street_geometry_matches_its_walk_measurement,
             prior_state="cross-street bearing and length were swapped after its profile was measured",
             neuter=_neuter_cross_street_arguments,
             neuters="cross-street construction swaps bearing and length"),
        Case("frontage_fallback_finds_ground_the_sweeps_miss", False,
             _case_frontage_fallback_finds_ground_the_sweeps_miss,
             prior_state="body-dependent anchors and 48 m sweeps miss an in-bounds legal pad",
             neuter=_neuter_frontage_fallback,
             neuters="only the original preferred frontage sweeps are enumerated"),
        Case("frontage_fallback_keeps_installation_refusal", False,
             _case_frontage_fallback_keeps_installation_refusal,
             prior_state="the only legal pad remains occupied throughout the fallback",
             neuter=_neuter_frontage_installation_gate,
             neuters="fallback placement ignores the recorded installation stand-off"),
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
             prior_state=("an operator's --expect-sites declares a site for "
                          "one solve: taking it from the search instead would "
                          "move a site somebody pinned on purpose"),
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
             neuters="the audit's own population as it stood: the solver's "
                     "records only, no membership test in either direction"),
        Case("declared_site_missing_refused", True,
             _case_a_declared_site_missing_from_the_publication_is_refused,
             prior_state=("this section's own run() judged the solver's "
                          "published records only (first the two towns, then "
                          "all 16), so a site a PERSON declared could vanish "
                          "from the artefact RoadNet reads and it would have "
                          "reported green -- the population error this "
                          "section exists to catch, in the check itself"),
             neuter=_neuter_the_audit_to_towns_only,
             neuters="the audit's own population as it stood: the solver's "
                     "records only, no membership test in either direction"),
        Case("declared_site_stale_request_refused", True,
             _case_a_declared_site_at_another_request_is_refused,
             prior_state=("a node measured for one request and published "
                          "after the declaration moved: RoadNet routes to "
                          "where the owner no longer asked for a road"),
             neuter=_neuter_the_audit_to_towns_only,
             neuters="the audit's own population as it stood: the solver's "
                     "records only"),
        Case("the_published_declared_set_is_accepted", False,
             _case_the_published_ulfsland_set_is_accepted,
             prior_state=("the direction a refuse-everything audit passes: "
                          "the complete publication -- 16 solver sites and "
                          "the 2 human-declared ones -- must not refuse, and "
                          "each declared site must be reported"),
             neuter=_neuter_the_audit_to_towns_only,
             neuters="the audit's own population as it stood: the solver's "
                     "records only, so the declared sites are never reported"),
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
        Case("declared_site_is_measured_and_claimed_first", False,
             _case_a_declared_site_is_measured_and_claims_its_district_first,
             prior_state=("Ulfsgard and Sudrvik were never sites of any "
                          "stage: harbour_city.py stood alone, the request "
                          "(1050, 720) lies under 3.19 m of sea, and no "
                          "solver site knew the district was spoken for"),
             neuter=_neuter_the_declared_district_is_not_claimed,
             neuters="the declared site measured and published but its "
                     "district never added to the claims"),
        Case("seed_without_declarations_is_unchanged", False,
             _case_a_seed_without_declarations_is_unchanged,
             prior_state=("a seed that declares nothing must publish a "
                          "byte-identical site set: no key, no claim"),
             neuter=_neuter_sites_yaml_always_publishes_declared,
             neuters="sites_yaml writing `declared_sites` for every seed"),
        Case("undeclared_sites_are_the_solvers", False,
             _case_undeclared_sites_are_the_solvers_to_the_coordinate,
             prior_state=("roads/spec.py::PINNED_SITES overrode 15 of the "
                          "solver's 16 sites on the default pair with the "
                          "answer of an earlier run: coordinates, pad radii, "
                          "datums"),
             neuter=_neuter_the_solver_is_pinned_again,
             neuters="the pin layer, applied over the solver on the default "
                     "pair"),
        Case("survey_tile_preserves_measured_road_reach", False,
             _case_a_survey_tile_preserves_measured_road_reach,
             prior_state="an added survey tile ID refused a reachable site "
                         "before consulting the covering rooted graph",
             neuter=_neuter_reach_to_the_measurement_window,
             neuters="the measurement-window island gate"),
        Case("survey_coverage_keeps_islet_and_pocket_refusals", True,
             _case_survey_coverage_does_not_root_an_islet_or_pocket,
             neuter=_neuter_the_road_is_taken_on_the_declaration,
             neuters="the declaration accepted without measured constraints"),
        Case("survey_coverage_keeps_approach_grade", True,
             _case_survey_coverage_keeps_approach_grade,
             neuter=_neuter_the_approach_is_not_asked,
             neuters="the rooted graph's snap accepted without approach grade"),
        Case("survey_coverage_requires_a_covering_root", True,
             _case_survey_coverage_without_a_root_is_not_a_road,
             neuter=_neuter_the_road_is_taken_on_the_declaration,
             neuters="the road declaration accepted without a covering root"),
        Case("survey_id_is_not_a_separate_landmass", False,
             _case_survey_id_is_not_a_separate_landmass,
             prior_state="a survey ID absent from the trunk label map was "
                         "reported as measured separate land",
             neuter=_neuter_landmass_to_the_measurement_window,
             neuters="missing source label map taken as separate land"),
        Case("survey_landmass_keeps_separate_and_unknown_distinct", False,
             _case_survey_landmass_keeps_separate_and_unknown_distinct,
             neuter=_neuter_landmass_unknown_to_separate,
             neuters="no measured spawn component reported as separate land"),
        Case("a_pocket_the_router_cannot_enter_is_portal_only", True,
             _case_a_pocket_the_router_cannot_enter_is_portal_only,
             prior_state=("Pirate68 with the pins out: wt-spawn (1432, 1546) "
                          "on the spawn landmass, in a 186-cell pocket of the "
                          "router's graph against 269,270 cells in the "
                          "window's largest strong component -- T10, T11 and "
                          "T12 came back UNROUTABLE"),
             neuter=_neuter_the_road_is_taken_on_the_declaration,
             neuters="road_priority taken as a road whatever the graph says"),
        Case("an_islet_across_the_water_is_portal_only", True,
             _case_an_islet_across_the_water_is_portal_only,
             prior_state=("Pirate68 with the pins out: wt-north, wt-east, "
                          "lh-east, lh-west, tree-west and tree-near on a "
                          "62,618-cell island of the graph; the router will "
                          "bridge a channel, which is how S1 and S2 join the "
                          "main continent, and that is not a licence to "
                          "bridge to every islet"),
             neuter=_neuter_the_landmass_is_not_asked,
             neuters="the graph's reach without the trunk-landmass test"),
        Case("a_pad_the_road_cannot_arrive_on_is_portal_only", True,
             _case_a_pad_the_road_cannot_arrive_on_is_portal_only,
             prior_state=("Pirate68 regen #6: lh-south, pad datum 33.27 m on "
                          "a sea-level shelf, ring ground 51-56 m; reached by "
                          "the graph, road ended 46.2 m short at 52.9 m "
                          "(69 % against a 12 % spur), batter refused with 89 "
                          "rim steps of 3.3 m, S11 unbuilt"),
             neuter=_neuter_the_approach_is_not_asked,
             neuters="the router snap alone deciding roaded"),
        Case("a_site_on_the_trunk_landmass_is_roaded", False,
             _case_a_site_on_the_trunk_landmass_is_roaded,
             prior_state=("the survivor: ground in the network's own component "
                          "on the trunk landmass"),
             neuter=_neuter_the_road_refuses_everything,
             neuters="a verdict that says portal-only for every site"),
        Case("declared_none_and_unrooted_windows_are_portal_only", True,
             _case_none_and_unrooted_are_portal_only,
             prior_state=("road_priority was published and read by nothing: "
                          "`none` and a window no crossing roots both read as "
                          "'a road, if one is typed'"),
             neuter=_neuter_the_road_is_taken_on_the_declaration,
             neuters="road_priority taken as a road whatever the graph says"),
        Case("the_solver_passes_over_what_no_road_reaches", True,
             _case_the_solver_passes_over_what_no_road_reaches,
             prior_state=("select_outliers ranked candidates by rise, relief, "
                          "coast and purity and never asked whether a road "
                          "can get there: 7 of 16 sites unroutable"),
             neuter=_neuter_the_solver_ignores_the_road,
             neuters="select_outliers called without a road verdict"),
        Case("no_reachable_candidate_leaves_the_site_portal_only", False,
             _case_no_reachable_candidate_leaves_the_site_portal_only,
             prior_state=("a building is never deleted and no road is "
                          "promised to ground none reaches: the site stands on "
                          "the best unreachable candidate and says so"),
             neuter=_neuter_the_solver_ignores_the_road,
             neuters="select_outliers called without a road verdict"),
        Case("names_are_bound_by_what_a_site_is_not_by_rank", True,
             _case_names_are_bound_by_what_a_site_is_not_by_rank,
             prior_state=("`wt-spawn` was whatever the search ranked first: "
                          "1.9 km from the StartTemple on Pirate68, and every "
                          "leg typed for one seed's `wt-spawn` ran to "
                          "another's accident"),
             neuter=_neuter_names_are_bound_by_search_rank,
             neuters="each name takes the next candidate the search ranked"),
        Case("a_name_belongs_on_its_window", True,
             _case_a_name_belongs_on_its_window,
             prior_state=("`lh-west`, `tree-west` and `tree-near` hang from "
                          "west-isle nodes and stood on another landmass: "
                          "W9, W10 and W11 were CROSS-ISLAND"),
             neuter=_neuter_names_are_bound_by_search_rank,
             neuters="each name takes the next candidate the search ranked, "
                     "in whichever window it stands"),
        Case("an_edge_to_a_portal_only_site_is_dropped_with_its_numbers", False,
             _case_a_portal_only_site_is_no_node_and_no_edge_names_it,
             prior_state=("a typed leg to a site the road verdict ruled out "
                          "was routed, found nothing, and was reported as an "
                          "UNROUTABLE edge"),
             neuter=_neuter_the_edge_is_kept_for_the_router_to_fail_on,
             neuters="the leg left in EDGES"),
        Case("an_edge_to_a_node_absent_for_no_reason_is_refused", True,
             _case_an_edge_to_a_node_absent_for_no_reason_is_refused,
             prior_state=("the pre-existing refusal, kept: a terminus that "
                          "vanishes with nothing recorded"),
             neuter=_neuter_the_missing_node_vanishes_quietly,
             neuters="the leg dropped without asking why"),
    ],
))
