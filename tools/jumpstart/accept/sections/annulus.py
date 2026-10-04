#!/usr/bin/env python3
"""ANNULUS - ground inside somebody's DECLARED extent that NOBODY has written.

THE DEFECT, MEASURED, AND WHY NO CHECK ON THIS WORLD COULD SEE IT.

Sample (-299, 214) sits one metre west of the portal hall's pad.  Its live
height is 35.350 m; its west neighbour (-300, 214) reads 37.100 m after T1's
rejoin and its east neighbour (-298, 214) reads 37.100 m on the hall's own
floor.  It is a 1.75 m pit, one metre wide, inside the hall apron, and it
alone took the portal hub from 1 of 28 destinations reachable to 0.

It is not a planning mistake.  It is ground that NO PARTICIPANT WAS ALLOWED TO
WRITE:

  * The PAD did not write it.  `portal-hall` seq 801 levelled up to
    (-298, 214) and stopped; (-299, 214) is 0.3 m outside its declared
    rectangle and its blob never modified it.  The sample's author is
    `road_S1_portalhub_brgs2` seq 417, which cut it -1.578 m and has not been
    back since.

  * The ROAD was forbidden to write it, TWICE OVER, by two instruments that
    both answered a question about a DECLARATION when they were asked a
    question about a WRITE:

      `ribbon.PadKeepOut.contains` matched the pad's NOMINAL rectangle plus
      apron (`pad_w / 2 + apron_m`), which is wider than the set of samples
      the pad levelled.  MEASURED over this chain: 21,666 lattice samples lie
      inside the 58 live pad rectangles and 3,564 of them have never been
      levelled by any pad -- 1,355 in `sudrberg-keep`, 666 of
      `stenvik-hall-1`'s 676.

      `applied.Applied.owners_at` walked all FOUR corners of the cell at
      (x, z) including the ones whose bilinear weight is ZERO, so asked at an
      exact lattice sample it reported the claims on three samples a whole
      metre away.  `foreign_at(-299.0, 214.0)` therefore answered
      `portal-hall#801` -- a one-sample annulus on the low side of every pad
      in the world -- and `ribbon.stamp`'s `refusal` returned "foreign".

So the pad does not write it, and the road may not.  The gap survives every
re-emit of every participant, for ever, and every existing check passes: the
pad's own footprint is flat, the road's own written samples are within
gradient, the keep-out is being honoured exactly as designed.

WHAT THIS SECTION ASSERTS.  Over the carriageway band of every built road
segment, within reach of a pad's declared extent: NO SAMPLE IS CLAIMED BY A
FOUNDATION THAT DID NOT LAY IT.  A claim on ground nobody levelled is a claim
nobody can discharge, and it is the shape of the defect rather than one
instance of it -- `flatten.zones_for` dropping edge rows under buildings is
the same sentence read from the pad's side.
"""
from __future__ import annotations

import math
import sys

from .. import instruments as I
from .. import termination as T
from ..registry import Case, Finding, Section, SectionResult, register

JUMPSTART = I.JUMPSTART
ROAD_ROLE = "road_segment"


# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# THE POPULATION IS `termination.emittable("annulus")`, and it includes one
# refusal THIS FILE DOES NOT CONTAIN: `corpus_is_about_this_world`, emitted
# by `instruments.read_scoped_corpus` on this section's behalf.  A coverage
# claim that read only this file would miss it, which is why the enumerator
# follows the call graph across module boundaries.
#
# TWO OF THE FOUR ARE THE FACTORY'S OWN ORDER, AND ONE IS NOT:
#
#   AN UNWRITTEN ANNULUS is ground a foundation CLAIMS and never LEVELLED,
#     so neither the claimant nor the road may write it.  The claimant's
#     own pad write is a `site_pad` this tree performs and it has not run
#     over those samples.  1E, and the row names the claimant.
#   A DECLARED RECTANGLE GATING GROUND NOBODY WROTE is the same mechanism
#     one level up: the pad's levelling has not covered what its rectangle
#     declares.  1E, naming the pad.
#   A PAD FOOTPRINT IN THE WRONG PLACE is NOT an order problem.  The
#     foundation is STANDING at the resolved centre and the records that
#     name it disagree; closing it means correcting a corpus and its
#     producer, or moving a building.  No tool in this tree collapses a
#     corpus and this tree never deletes a building.  1C.
#   A CORPUS ABOUT ANOTHER WORLD is a reading of a DIFFERENT WORLD, not
#     contested ground: re-run the producer for this one.  1E.
TERMINATION_FAMILIES = {
    "no_unwritten_annulus": lambda nums: (
        "UPSTREAM_STAGE_NOT_RUN",
        f"the claimant {nums.get('first_claim') or 'foundation'} must LEVEL "
        f"the {nums.get('samples')} carriageway sample(s) it claims - its "
        f"own site_pad terrain write, which this tree performs - or the "
        f"claim must shrink to the set it did level"),
    "keepout_is_the_levelled_set": lambda nums: (
        "UPSTREAM_STAGE_NOT_RUN",
        f"{nums.get('rect_gated')} live pad(s) gate the carriageway by a "
        f"DECLARED rectangle rather than by the {nums.get('levelled')} "
        f"sample(s) they levelled; the prerequisite is each pad's own "
        f"site_pad write over the rectangle it declares"),
    "pad_footprint_is_where_the_site_is": (
        "SOURCE_CHANGE",
        "the foundation is standing at the resolved centre and the site's "
        "own records name another; closing it means correcting the records "
        "and their producer, or moving a building - no tool in this tree "
        "collapses a corpus and this tree never deletes a building"),
    "corpus_is_about_this_world": (
        "PRODUCER_NOT_RUN",
        "re-run the producer of this corpus for this world and seed; a "
        "corpus that declares another world is a reading of a DIFFERENT "
        "WORLD, not a defect of this one's ground"),
}


# ---------------------------------------------------------------------------
# THE RULE, as one pure function, so the selftest cases exercise the rule the
# gate applies rather than a restatement of it.
# ---------------------------------------------------------------------------
def unowned_claims(band, claim_at, authors_at) -> list[tuple]:
    """Every sample in `band` a FOUNDATION claims but never LEVELLED.

    `claim_at(sx, sz) -> name | None` is whoever forbids the road this
    sample: the pad keep-out, or a foreign (non-road) write owning it.
    `authors_at(sx, sz) -> iterable of role strings` is the role of every
    write that AUTHORED this exact sample -- changed its value, as opposed to
    carrying it forward in a union or declaring a rectangle over it.

    A claim is DISCHARGEABLE when some non-road write authored the sample:
    there is a floor there, it is the pad's, and the road grading into its
    edge is the correct answer.  A claim with no non-road author is the
    annulus: unwritten ground that the declaring party will never write and
    the road is not allowed to.
    """
    out = []
    for sx, sz in band:
        who = claim_at(sx, sz)
        if who is None:
            continue
        if any(r != ROAD_ROLE for r in authors_at(sx, sz)):
            continue
        out.append((sx, sz, who))
    return out


def band_samples(seg: dict, near, margin_m: float = 2.0) -> list[tuple]:
    """The lattice samples under one segment's carriageway plus shoulder.

    Scoped to samples within `margin_m` of some pad's declared extent,
    because outside every declaration there is nothing to claim them.  That
    scoping is what makes this section a few thousand samples rather than the
    350,000 in the full corridor, and it cannot hide an annulus sample: an
    annulus sample is by construction inside or adjacent to a declaration.
    """
    half = seg["half_width_m"] + seg["shoulder_m"]
    out: set[tuple] = set()
    nodes = seg["nodes"]
    for (ax, az), (bx, bz) in zip(nodes, nodes[1:]):
        dx, dz = bx - ax, bz - az
        run = math.hypot(dx, dz)
        if run < 1e-9:
            continue
        ux, uz = dx / run, dz / run
        nx, nz = -uz, ux
        steps = int(run) + 1
        lat = int(half) + 1
        for k in range(steps + 1):
            s = min(k, run)
            cx, cz = ax + ux * s, az + uz * s
            if not any(abs(cx - q[1]) <= q[3] + half + margin_m
                       and abs(cz - q[2]) <= q[4] + half + margin_m
                       for q in near):
                continue
            for j in range(-lat, lat + 1):
                out.add((int(math.floor(cx + nx * j + 0.5)),
                         int(math.floor(cz + nz * j + 0.5))))
    return sorted(out)


# ---------------------------------------------------------------------------
# the live run
# ---------------------------------------------------------------------------
def _instruments():
    for sub in ("ledger", "roads", "terraform"):
        p = str(JUMPSTART / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import applied as appliedmod          # noqa: PLC0415
    import ribbon as ribbonmod            # noqa: PLC0415
    import writer as writermod            # noqa: PLC0415
    return appliedmod, ribbonmod, writermod


def run(ctx) -> SectionResult:
    from .roads import built_segments     # noqa: PLC0415
    appliedmod, ribbonmod, writermod = _instruments()

    # THE CORPUS, THROUGH THE ONE GATE-SIDE GUARD.  Until 2026-09-23 this
    # line was `yaml.safe_load((JUMPSTART / "settlements" / "sites.yaml")
    # .read_text())` with no scope check at all, so on `--world Fresh42`
    # ULFSLAND'S PAD SET was joined to a Fresh42 chain and every keep-out
    # this section honours came from a world the run was not asked about.
    # It is resolved by `roads/spec.py::sites_path` through the townsites
    # section's own accessor, so a seed that has solved its own site set is
    # read from `settlements/sites.<seed>.yaml` rather than refused.
    from .townsites import _sites_path    # noqa: PLC0415
    doc, refusals = I.read_scoped_corpus(
        ctx, "the published site set", resolve=_sites_path,
        produced_by="settlements/plan.py::build")
    if refusals:
        # AN EARLY RETURN IS STILL A BOARD ROW - and this one is not even
        # emitted by this file, which is exactly why the enumerator behind
        # the coverage claim follows the call graph rather than the module.
        return SectionResult(findings=refusals + T.classify(
            sys.modules[__name__], refusals, "annulus"))

    # AND THE LEDGER IS THE ONE THIS RUN WAS POINTED AT.  `Ledger.open(world)`
    # alone resolves `ledger/runs/<world>` and IGNORES `--ledger`, so a run
    # driven against a ledger somewhere else read a different chain here than
    # every other section read - two worlds joined by one verdict, which is
    # the fault this gate's own snapshot rule exists to prevent.
    led = writermod.Ledger.open(ctx.args.world, actor="accept-annulus",
                                root=ctx.ledger_dir)
    recs = led.records()
    sites = doc["sites"] if isinstance(doc, dict) else doc
    pk = ribbonmod.pad_keepouts_from_chain(sites, recs, led)
    live = appliedmod.Applied(actor="accept-annulus")
    near = list(pk.rects)

    def claim_at(sx, sz):
        sid = pk.contains(float(sx), float(sz))
        if sid is not None:
            return f"pad keep-out {sid}"
        w = live.foreign_at(float(sx), float(sz))
        return None if w is None else f"{w['name']}#{w['seq']} ({w['role']})"

    def authors_at(sx, sz):
        return [w["role"] for w in live.holders(int(sx), int(sz))]

    segs = built_segments(ctx.ledger)
    findings: list[Finding] = []
    rows: list[tuple] = []
    scanned = 0
    for name in sorted(segs):
        seg = segs[name]
        band = band_samples(seg, near)
        scanned += len(band)
        for sx, sz, who in unowned_claims(band, claim_at, authors_at):
            rows.append((seg["seg"], sx, sz, who))

    by_seg: dict[str, list] = {}
    for sg, sx, sz, who in rows:
        by_seg.setdefault(sg, []).append((sx, sz, who))
    for sg in sorted(by_seg):
        pts = by_seg[sg]
        sx, sz, who = pts[0]
        findings.append(Finding(
            check="no_unwritten_annulus", ok=False,
            message=(f"{sg}: {len(pts)} carriageway sample(s) are CLAIMED by "
                     f"a foundation that never levelled them, so neither the "
                     f"claimant nor the road may write them; first "
                     f"({sx}, {sz}) claimed by {who}"),
            where=(sx, sz), measured=float(len(pts)), limit=0.0,
            numbers={"samples": len(pts), "first_claim": who}))
    if not rows:
        findings.append(Finding(
            check="no_unwritten_annulus", ok=True,
            message=(f"{scanned} carriageway sample(s) within reach of "
                     f"{len(near)} declared pad extent(s) over {len(segs)} "
                     f"built segment(s): every claim is on ground its "
                     f"claimant levelled"),
            measured=0.0, limit=0.0,
            numbers={"scanned": scanned, "pads": len(near),
                     "segments": len(segs)}))

    # THE KEEP-OUT ITSELF, independent of any road: a declared rectangle
    # wider than what the pad wrote is the annulus whether or not a road
    # happens to run through it today, and the next segment planned across
    # one would inherit it.
    unlaid = len(pk.unlaid)
    findings.append(Finding(
        check="keepout_is_the_levelled_set",
        ok=(unlaid == 0),
        message=(f"{unlaid} live pad(s) still gate the carriageway by their "
                 f"DECLARED rectangle (pad_w/2 + apron_m) rather than by the "
                 f"{len(pk.levelled)} sample(s) the pads actually levelled"
                 if unlaid else
                 f"the carriageway keep-out is {len(pk.levelled)} sample(s) "
                 f"{len(pk.rects)} live pad(s) actually levelled; 0 declared "
                 f"rectangle(s) gate ground nobody wrote"),
        measured=float(unlaid), limit=0.0,
        numbers={"pads": len(pk.rects), "levelled": len(pk.levelled),
                 "rect_gated": unlaid}))

    # WHERE THE FOOTPRINT IS, against where the site says it is.  A pad
    # rectangle in the wrong place is the annulus MOVED BODILY: it protects
    # ground nobody built on and leaves the real foundation edge outside
    # every rectangle.  `_pad_footprint` reads the position off a sibling
    # record, so this compares the resolved centre against the position the
    # SITE's own records and `settlements/placements/*.yaml` state.
    disp = pad_displacements(pk, recs)
    over = [d for d in disp if d["off_m"] > 0.05]
    if over:
        worst = max(over, key=lambda d: d["off_m"])
        findings.append(Finding(
            check="pad_footprint_is_where_the_site_is", ok=False,
            message=(f"{len(over)} of {len(disp)} live pad footprint(s) are "
                     f"resolved to a centre the site's own records do not "
                     f"name; worst {worst['site_id']} {worst['off_m']:.2f} m "
                     f"from {worst['said']} -- a {2 * worst['hw']:.1f} x "
                     f"{2 * worst['hd']:.1f} m foundation over ground nobody "
                     f"built on"),
            where=tuple(worst["resolved"]), measured=worst["off_m"],
            limit=0.05,
            numbers={"displaced": len(over), "compared": len(disp),
                     "worst_site": worst["site_id"],
                     "total_displacement_m": round(
                         sum(d["off_m"] for d in over), 2)}))
    else:
        findings.append(Finding(
            check="pad_footprint_is_where_the_site_is", ok=True,
            message=(f"{len(disp)} live pad footprint(s) resolve to the "
                     f"centre the site's own zones_generate / objects_clear / "
                     f"spawn_plan records name, within 0.05 m"),
            measured=0.0, limit=0.05, numbers={"compared": len(disp)}))
    # AND EVERY REFUSAL ABOVE GETS ITS CLASS, THROUGH THE ONE CLASSIFIER.
    findings += T.classify(sys.modules[__name__], findings, "annulus")
    return SectionResult(findings=findings)


def pad_displacements(pk, records) -> list[dict]:
    """Per live pad: how far its RESOLVED footprint centre sits from the
    position the site's own extent records name.

    The reference is the LAST `zones_generate` / `objects_clear` /
    `spawn_plan` the site wrote at or before its pad write -- the same three
    ops `writer._SITE_EXTENT_OPS` names, asked here INDEPENDENTLY of
    `_pad_footprint` so that the check is a second reading rather than a
    restatement of the thing it is judging.
    """
    _a, _r, writermod = _instruments()
    pads = {s: {"site_id": s, "x": x, "z": z, "hw": hw, "hd": hd}
            for s, x, z, hw, hd in pk.rects}
    seq_line = {}
    for line, r in enumerate(records):
        sid = (r.get("params") or {}).get("site_id")
        if r.get("op") == "terrain_write" and sid in pads:
            seq_line[sid] = line
    out = []
    for sid, q in pads.items():
        line = seq_line.get(sid)
        if line is None:
            continue
        said = None
        for r in reversed(records[:line + 1]):
            if r.get("op") not in writermod._SITE_EXTENT_OPS:
                continue
            rp = r.get("params") or {}
            if rp.get("site_id") != sid:
                continue
            xz = writermod._record_xz(rp)
            if xz:
                said = (float(xz[0]), float(xz[1]))
                break
        if said is None:
            continue
        out.append({"site_id": sid, "resolved": [q["x"], q["z"]],
                    "said": [round(said[0], 2), round(said[1], 2)],
                    "hw": q["hw"], "hd": q["hd"],
                    "off_m": round(math.hypot(q["x"] - said[0],
                                              q["z"] - said[1]), 3)})
    return out


# ---------------------------------------------------------------------------
# SELFTEST - the state this world was actually in
# ---------------------------------------------------------------------------
# The portal hall apron, as MEASURED off the chain at
# chain 65bf1082..., seq 5133: the pad levelled (-298, 214) eastward and
# stopped; (-299, 214) is authored by road_S1_portalhub_brgs2 seq 417.
_PAD_LEVELLED = {(-298, 214), (-297, 214), (-296, 214), (-298, 215)}
_AUTHORS = {(-299, 214): [ROAD_ROLE], (-300, 214): [ROAD_ROLE],
            (-298, 214): ["portal_hall_pad"], (-297, 214): ["portal_hall_pad"],
            (-296, 214): ["portal_hall_pad"], (-298, 215): ["portal_hall_pad"]}
_BAND = [(-301, 214), (-300, 214), (-299, 214), (-298, 214)]


def _authors_at(sx, sz):
    return _AUTHORS.get((sx, sz), [])


def _claim_blind_to_weight(sx, sz):
    """THE OLD INSTRUMENT: `owners_at` walking all four corners of the cell,
    including the ones whose bilinear weight is zero.  This is the code that
    shipped, and it answers `portal-hall` at (-299, 214)."""
    for dx in (0, 1):
        for dz in (0, 1):
            for role in _AUTHORS.get((sx + dx, sz + dz), []):
                if role != ROAD_ROLE:
                    return f"portal-hall ({role})"
    return None


def _claim_weighted(sx, sz):
    """THE FIXED INSTRUMENT: only samples the point is blended FROM.  At an
    exact lattice sample that is the sample itself."""
    for role in _AUTHORS.get((sx, sz), []):
        if role != ROAD_ROLE:
            return f"portal-hall ({role})"
    return None


def _claim_by_declared_rectangle(sx, sz):
    """THE OLD KEEP-OUT: the nominal rectangle plus apron, which reaches
    x >= -298.7 - EDGE_EPS and so swallows nothing at (-299, 214) by itself --
    but the hall's rectangle in `_pad_claims` is 18.7 m half-extent about
    (-280, 216), and `stenvik-hall-1`'s swallows 666 of its 676 samples.
    Modelled here as: inside the declared rectangle, levelled or not."""
    return ("portal-hall (declared rectangle)"
            if -299 <= sx <= -262 and 198 <= sz <= 234 else None)


def _f(ok, msg, **kw):
    return Finding(check="no_unwritten_annulus", ok=ok, message=msg, **kw)


def _case_known_bad_apron():
    """(-299, 214) on the day the hub went to 0 of 28 destinations."""
    hits = unowned_claims(_BAND, _claim_blind_to_weight, _authors_at)
    return [_f(False, f"{len(hits)} unowned claim(s), first {hits[0][:2]}",
               where=hits[0][:2], measured=float(len(hits)), limit=0.0)] \
        if hits else [_f(True, "no unowned claims", measured=0.0, limit=0.0)]


def _case_declared_rectangle_swallows_unwritten_ground():
    """A rectangle used as a foundation claims ground the pad never laid."""
    hits = unowned_claims(_BAND, _claim_by_declared_rectangle, _authors_at)
    return [_f(False, f"{len(hits)} unowned claim(s), first {hits[0][:2]}",
               where=hits[0][:2], measured=float(len(hits)), limit=0.0)] \
        if hits else [_f(True, "no unowned claims", measured=0.0, limit=0.0)]


def _case_weighted_instrument_clears_the_apron():
    """The fix: the road may write (-299, 214), the pad keeps (-298, 214)."""
    hits = unowned_claims(_BAND, _claim_weighted, _authors_at)
    return [_f(False, f"{len(hits)} unowned claim(s), first {hits[0][:2]}",
               where=hits[0][:2], measured=float(len(hits)), limit=0.0)] \
        if hits else [_f(True, "no unowned claims over 4 samples",
                         measured=0.0, limit=0.0,
                         numbers={"scanned": len(_BAND)})]


def _case_a_real_pad_floor_is_still_refused():
    """The rule must NOT open a pad's actual floor: (-298, 214) is levelled
    by the hall, the claim is dischargeable, and it stays the pad's."""
    hits = unowned_claims([(-298, 214), (-297, 214)], _claim_weighted,
                          _authors_at)
    if hits:
        return [_f(False, f"a pad floor was reported as annulus: {hits[0]}",
                   where=hits[0][:2], measured=float(len(hits)), limit=0.0)]
    return [_f(True, "2 pad-floor samples, 0 reported", measured=0.0,
               limit=0.0, numbers={"scanned": 2})]


def _case_keepout_rectangle_is_reported():
    """`keepout_is_the_levelled_set` must refuse a keep-out that still gates
    by declaration.  Built from `PadKeepOut` itself, not a restatement."""
    _a, ribbonmod, _w = _instruments()
    pk = ribbonmod.PadKeepOut(
        rects=[("portal-hall", -280.0, 216.0, 18.7, 18.7)],
        discs=[], by_id={}, levelled={}, undecodable=None,
        laid_sites=set())
    n = len(pk.unlaid)
    return [Finding(check="keepout_is_the_levelled_set", ok=(n == 0),
                    message=f"{n} declared rectangle(s) still gate",
                    measured=float(n), limit=0.0)]


def _case_keepout_from_a_pad_that_laid_ground():
    _a, ribbonmod, _w = _instruments()
    pk = ribbonmod.PadKeepOut(
        rects=[("portal-hall", -280.0, 216.0, 18.7, 18.7)],
        discs=[], by_id={}, levelled={(-298, 214): "portal-hall"},
        undecodable=None, laid_sites={"portal-hall"})
    n = len(pk.unlaid)
    hit = pk.contains(-299.0, 214.0)
    ok = (n == 0 and hit is None and pk.contains(-298.0, 214.0)
          == "portal-hall")
    return [Finding(check="keepout_is_the_levelled_set", ok=ok,
                    message=(f"{n} rectangle(s) gate; (-299,214) -> {hit}, "
                             f"(-298,214) -> {pk.contains(-298.0, 214.0)}"),
                    measured=float(n), limit=0.0)]


# THE TWO RECORD SHAPES THAT MOVED 16 OF 58 FOOTPRINTS, verbatim from this
# chain.  `stenvik-cottage-2` reserved (484.7, 901.7), was nudged, reserved
# (477.7, 902.7) and built there; `portal-hall` cleared (-280.0, 216.0) and
# then recorded EIGHTEEN portal arches around a 26 m ring before its pad.
def _nudged_site_records():
    return [
        {"seq": 127, "op": "zones_generate",
         "params": {"site_id": "stenvik-cottage-2", "pos": [484.7, 901.7]}},
        {"seq": 260, "op": "zones_generate",
         "params": {"site_id": "stenvik-cottage-2", "pos": [477.7, 902.7]}},
        {"seq": 265, "op": "terrain_write",
         "params": {"site_id": "stenvik-cottage-2", "role": "site_pad",
                    "name": "stenvik-cottage-2", "pad_w": 10.4, "pad_d": 12.4,
                    "apron_m": 0.0, "target_y": 45.63, "entries": []}},
    ]


def _ringed_site_records():
    recs = [{"seq": 764, "op": "objects_clear",
             "params": {"site_id": "portal-hall", "role": "portal_hall_pad",
                        "centre": [-280.0, 216.0]}}]
    ring = [(-273.66, 228.73), (-269.20, 225.72), (-266.22, 221.27),
            (-265.17, 215.98), (-266.22, 210.70), (-269.24, 206.22),
            (-273.71, 203.25), (-278.98, 202.24), (-284.26, 203.28),
            (-288.73, 206.27), (-291.70, 210.74), (-291.71, 221.28),
            (-288.63, 225.88), (-284.24, 228.75), (-278.93, 229.78),
            (-278.04, 224.01), (-273.80, 220.25), (-271.279, 214.195)]
    for i, (x, z) in enumerate(ring):
        recs.append({"seq": 766 + 2 * i, "op": "portal",
                     "params": {"site_id": "portal-hall",
                                "role": "portal_hall_end", "pos": [x, 37.1, z]}})
    recs.append({"seq": 801, "op": "terrain_write",
                 "params": {"site_id": "portal-hall", "role": "portal_hall_pad",
                            "name": "portal-hall", "pad_w": 37.4, "pad_d": 37.4,
                            "apron_m": 0.0, "target_y": 37.1, "entries": []}})
    return recs


def _resolved(recs):
    _a, _r, writermod = _instruments()
    foot = writermod._pad_footprint(recs[-1], recs)
    return (foot["x"], foot["z"]) if foot else None


def _pos_finding(got, want, what):
    off = 1e9 if got is None else math.hypot(got[0] - want[0], got[1] - want[1])
    return [Finding(check="pad_footprint_is_where_the_site_is",
                    ok=(off <= 0.05),
                    message=(f"{what}: footprint resolved to {got}, the "
                             f"site's own records say {want}, off "
                             f"{off:.3f} m"),
                    where=tuple(got) if got else None,
                    measured=round(off, 3), limit=0.05)]


def _case_first_sibling_takes_the_abandoned_attempt():
    """THE SHIPPED RESOLVER: scan FORWARD, take the first sibling with an xz.
    On a nudged site that is the reservation it walked away from."""
    recs = _nudged_site_records()
    got = None
    for r in recs:
        _a, _r, wm = _instruments()
        xz = wm._record_xz(r["params"])
        if r["params"].get("site_id") == "stenvik-cottage-2" and xz:
            got = (float(xz[0]), float(xz[1]))
            break
    return _pos_finding(got, (477.7, 902.7), "first-sibling resolver")


def _case_last_sibling_takes_one_portal_arch():
    """THE OBVIOUS REPAIR, ALSO WRONG: scan BACKWARD over EVERY sibling.  On
    the portal hall the last one is an arch on the ring, not the hall."""
    recs = _ringed_site_records()
    got = None
    for r in reversed(recs):
        _a, _r, wm = _instruments()
        xz = wm._record_xz(r.get("params") or {})
        if (r.get("params") or {}).get("site_id") == "portal-hall" and xz:
            got = (float(xz[0]), float(xz[1]))
            break
    return _pos_finding(got, (-280.0, 216.0), "last-any-sibling resolver")


def _case_nudged_site_resolves_to_the_attempt_it_built():
    return _pos_finding(_resolved(_nudged_site_records()), (477.7, 902.7),
                        "stenvik-cottage-2")


def _case_ringed_site_resolves_to_the_hall_not_an_arch():
    return _pos_finding(_resolved(_ringed_site_records()), (-280.0, 216.0),
                        "portal-hall")


# ---------------------------------------------------------------------------
# THE SAME ZERO-WEIGHT CORNER, ON THE OTHER READER
# ---------------------------------------------------------------------------
# `owners_at` was fixed for it above.  `Applied.surface_at` and
# `generated_at` were NOT, and they are the readers that answer "what is the
# ground here": both walked all four bilinear corners and returned None if
# ANY of their zones was missing from the caller's patch set, checking the
# weight only afterwards.  So an INTEGER query - every lattice sample, every
# pad row, every road station - was refused whenever one of its three
# weight-ZERO neighbours fell in the next zone.
#
# MEASURED 2026-09-22 on Ulfsland b608a61b: pad `stenvik-hut-1`, 10.4 x 10.4 m
# at (538.3, 897.2), lies wholly inside zone (8,14) and its own patch set
# holds that zone -- yet 11 of its 121 lattice vertices came back None, all of
# them the x = 543 column, whose weight-0 neighbour x = 544 is row 0 of zone
# (9,14).  With the corner skipped, 0 of 121, and (543, 892) reads 48.2750.
# `relight.zones_around` carries a comment describing the work-around it does
# to avoid this, which is how a defect survives: every caller pays for it
# privately.
_HUT_ZONE = "z_8_14"


def _patch_stub(tcdata):
    """A generated-height patch that answers one constant, so the CASE is
    about coverage and nothing else."""
    return type("StubPatch", (), {
        "n": tcdata.PITCH,
        "at": lambda self, i, j: 48.27496337890625})()


def _reader(appliedmod):
    """THE REAL `Applied.surface_at`, bound to an object that supplies only
    `delta_at` - this case is about which corners decide coverage, not about
    the blobs, and constructing a live `Applied` would open the ledger."""
    return type("Reader", (), {
        "delta_at": staticmethod(lambda x, z: 0.0),
        "surface_at": appliedmod.Applied.surface_at})()


def _surface_blind_to_weight(tcdata, patches, x, z):
    """THE CODE THAT SHIPPED: every corner gates coverage, weight considered
    afterwards."""
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    gen = 0.0
    for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                      (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
        sx, sz = x0 + dx, z0 + dz
        zx, zz = tcdata.zone_of(sx, sz)
        patch = patches.get(f"z_{zx}_{zz}")
        if patch is None:
            return None
        if w:
            gen += w * float(patch.at(0, 0))
    return gen, gen, 0.0


def _zw_setup():
    appliedmod, _ribbon, _writer = _instruments()
    import tcdata                          # noqa: PLC0415
    return appliedmod, tcdata, {_HUT_ZONE: _patch_stub(tcdata)}


def _sf(ok, msg, **kw):
    return Finding(check="zero_weight_corner", ok=ok, message=msg, **kw)


def _case_zero_weight_corner_refuses_a_covered_sample():
    """THE KNOWN-BAD STATE.  (543, 892) is inside zone (8,14), the patch set
    holds (8,14), and the shipped reader answers None because (544, 892) --
    weight 0.0 -- is in (9,14)."""
    appliedmod, tcdata, patches = _zw_setup()
    got = _surface_blind_to_weight(tcdata, patches, 543.0, 892.0)
    if got is not None:
        return [_sf(True, "the blind reader answered, so this case proves "
                          "nothing")]
    return [_sf(False,
                "surface_at refused (543.0, 892.0): the sample is in zone "
                "(8,14), which the patch set holds, and the only absent zone "
                "is (9,14) -- reached through a corner of bilinear weight "
                "0.0 that contributes nothing to the answer",
                where=[543.0, 892.0], measured=0.0, limit=1.0,
                numbers={"vertices_refused": 11, "vertices_in_pad": 121,
                         "pad": "stenvik-hut-1", "absent_zone": [9, 14],
                         "corner_weight": 0.0})]


def _case_the_shipping_reader_answers_it():
    """THE FIX, through the REAL method rather than a restatement of it."""
    appliedmod, tcdata, patches = _zw_setup()
    got = _reader(appliedmod).surface_at(patches, 543.0, 892.0)
    if got is None:
        return [_sf(False, "the shipping reader still refuses a covered "
                           "lattice sample at (543.0, 892.0)",
                    where=[543.0, 892.0], measured=0.0, limit=1.0)]
    return [_sf(True, f"(543.0, 892.0) reads {got[0]:.4f} from zone (8,14) "
                      f"alone", measured=1.0, limit=1.0,
                numbers={"applied": round(got[0], 4)})]


def _case_a_weight_bearing_corner_still_refuses():
    """AND THE FIX MUST COST NOTHING.  Off the lattice at x = 543.5 the
    corner in (9,14) carries weight 0.5, so its absence really does mean
    there is no answer -- a reader that returned a half-blend of one corner
    would be inventing ground."""
    appliedmod, tcdata, patches = _zw_setup()
    got = _reader(appliedmod).surface_at(patches, 543.5, 892.0)
    if got is not None:
        return [_sf(False,
                    f"(543.5, 892.0) answered {got[0]:.4f} with zone (9,14) "
                    f"absent, though that corner carries weight 0.5",
                    where=[543.5, 892.0], measured=1.0, limit=0.0)]
    return [_sf(True, "an absent zone under a weight-bearing corner is still "
                      "None", measured=0.0, limit=0.0)]


register(Section(
    name="annulus",
    measures=("that no carriageway sample is claimed by a foundation which "
              "never levelled it -- the unwritten annulus between a declared "
              "pad extent and the samples the pad actually wrote -- and that "
              "each pad's declared extent is resolved to the centre its own "
              "site records name"),
    instrument=("roads/applied.py per-sample AUTHORSHIP off the ledger blobs "
                "(the last claim that CHANGED each sample), "
                "roads/ribbon.py pad_keepouts_from_chain, and a second "
                "independent read of ledger/writer.py _SITE_EXTENT_OPS"),
    population=("every lattice sample under the carriageway plus shoulder of "
                "every road segment the ledger records a terrain write for, "
                "restricted to samples within 2 m of a declared pad extent; "
                "and every live pad claim in the chain"),
    run=run,
    cases=[
        Case("known_bad_apron_minus299_214", True, _case_known_bad_apron,
             prior_state=("the portal hall apron on 2026-09-21: the pad "
                          "levelled to (-298,214), road S1 seq 417 authored "
                          "(-299,214) at -1.578 m, and owners_at's "
                          "zero-weight corners made the pad claim it -- "
                          "0 of 28 destinations reachable")),
        Case("declared_rectangle_swallows_unwritten_ground", True,
             _case_declared_rectangle_swallows_unwritten_ground,
             prior_state=("3,564 of 21,666 samples inside the 58 live pad "
                          "rectangles have never been levelled by any pad")),
        Case("keepout_still_gates_by_declaration", True,
             _case_keepout_rectangle_is_reported,
             prior_state=("PadKeepOut.contains matched pad_w/2 + apron_m for "
                          "every live pad, laid or not")),
        Case("weighted_instrument_clears_the_apron", False,
             _case_weighted_instrument_clears_the_apron),
        Case("a_real_pad_floor_is_still_refused", False,
             _case_a_real_pad_floor_is_still_refused),
        Case("keepout_from_a_pad_that_laid_ground", False,
             _case_keepout_from_a_pad_that_laid_ground),
        Case("first_sibling_takes_the_abandoned_attempt", True,
             _case_first_sibling_takes_the_abandoned_attempt,
             prior_state=("16 of 58 live pad footprints resolved to an "
                          "abandoned first reservation, 92.07 m of "
                          "displacement in total, worst wt-north 12.53 m")),
        Case("last_sibling_takes_one_portal_arch", True,
             _case_last_sibling_takes_one_portal_arch,
             prior_state=("reversing the scan alone moves the portal hall's "
                          "37.4 x 37.4 m footprint 8.9 m east onto an arch "
                          "of its own 26 m portal ring")),
        Case("nudged_site_resolves_to_the_attempt_it_built", False,
             _case_nudged_site_resolves_to_the_attempt_it_built),
        Case("ringed_site_resolves_to_the_hall_not_an_arch", False,
             _case_ringed_site_resolves_to_the_hall_not_an_arch),
        Case("zero_weight_corner_refuses_a_covered_sample", True,
             _case_zero_weight_corner_refuses_a_covered_sample,
             prior_state=("MEASURED 2026-09-22 on Ulfsland b608a61b: pad "
                          "stenvik-hut-1 lies wholly inside zone (8,14) and "
                          "its own patch set holds it, yet 11 of its 121 "
                          "lattice vertices read None from "
                          "Applied.surface_at -- the whole x = 543 column, "
                          "refused through a bilinear corner of weight 0.0 "
                          "in zone (9,14). The same defect owners_at was "
                          "fixed for, on the reader that answers 'what is "
                          "the ground here', and relight.zones_around "
                          "over-fetches a ring of zones to work around it")),
        Case("the_shipping_reader_answers_it", False,
             _case_the_shipping_reader_answers_it),
        Case("a_weight_bearing_corner_still_refuses", False,
             _case_a_weight_bearing_corner_still_refuses),
    ],
))
