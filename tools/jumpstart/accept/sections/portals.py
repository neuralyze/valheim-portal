#!/usr/bin/env python3
"""PORTALS - a tag is a destination, a board is a promise, and an arch you
cannot walk into is furniture.

FOUR THINGS ARE CHECKED AND NOT ONE OF THEM IS GEOMETRY ALONE.

  1. TAG ENDS, WORLD-WIDE.  Vanilla pairing is EXACT STRING EQUALITY with a
     uniform random draw among equally-tagged unconnected portals.  So a tag
     with one end pairs with nothing; a tag with three ends makes the
     destination a coin toss; and a BLANK tag joins the pool of the world's
     blank-tag mod-location portals and sends the operator somewhere nobody
     chose.  The invariant is 2 ENDS PER TAG and it is checked GLOBALLY,
     because TWO AGENTS CAN BREAK IT INDEPENDENTLY - one owns the hall end,
     one owns the tower end, each is correct about its own site, and the
     world is wrong.  A per-site check cannot see that by construction.

     And it is checked against the WORLD, not against the ledger.  The
     ledger's own close-report counts RECORDS: on this world that method
     reported 19 of 20 tags one-ended when all 20 were two-ended, because
     nineteen tags had three records and two live ends.  Records are
     intent; ZDOs are the world.

  2. THE TAG SURVIVED THE REPLACE.  A portal is retired and re-spawned by
     `deleteObjects` + `spawn_object <prefab> ... data=<data_b64>`, and the
     tag rides in that payload.  Drop it and the arch comes back ANONYMOUS -
     same prefab, same position, same rotation, no tag - and every check
     that counts arches still passes.  So the ledger's live payload is
     DECODED here (ZDO string section: uint32 flags, byte count, then
     int32 key hash + varint-length UTF-8 per entry) and compared with the
     tag the WORLD reports at that position.  Verbatim or it is a finding.

  3. THE BOARD NAMES ITS OWN ARCH.  16 of 17 guideposts were once nearer the
     WRONG arch.  A check that binds a board to its arch by DISTANCE cannot
     see that - it confirms itself.  So the bind is by TEXT: either the
     board's own text carries a live tag token verbatim, or the ledger's
     record of that exact string says which tag it was written for.  Then
     the geometry is asked ONE question it cannot answer for itself: is the
     board nearest to an end of the tag it names?

  4. THE APPROACH IS A RAY, NOT A VOLUME.  The operator's report was "the
     signs in the portal hub are placed inside the archways which blocks the
     approach into the portal".  Eleven of the fifteen boards sat inside a
     2.94 x 3.00 x 2.00 m corridor volume; the OTHER FOUR stood 4.395 m out
     along the same facing axis and CLEARED that volume by 0.150 m - and a
     walker still had to walk through them.  A clearance smaller than the
     uncertainty in the volume's own definition is not clearance.  Here the
     walker's own measured capsule is SWEPT along the arch's facing axis and
     the first overlap ends the approach.

WHAT THIS SECTION NEVER DOES: it never reads a tag from the plan, never
concludes "no readable tags, therefore no untagged portals", and never
deletes a board.  Where the string instrument is missing it REFUSES and
names it.
"""
from __future__ import annotations

import base64
import json
import math
import re
import struct
import sys

from . import _approach as A
from .. import termination as T
from ..registry import Case, Finding, Section, register

# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# THE POPULATION IS `termination.emittable("portals")`, read out of this
# file's own parse tree, and `termination.coverage` refuses the gate's
# selftest if any of it is missing from this table.
#
# THREE REMEDY SHAPES, AND ONE OF THEM IS NOT THIS SECTION'S TO PERFORM:
#
#   A TAG IS WRONG, MISSING OR DOUBLED, OR A BOARD NAMES THE WRONG DOOR.
#     `settlements/portal_hall.py` retires an arch and re-places it with
#     the tag it should carry - ledger seq 5074 is one such retire-and-
#     replace - and a board is placed the same way.  No terrain is written
#     and the piece is replaced rather than removed, which is every clause
#     of ACTIONABLE.
#   THE TAG CENSUS WAS NEVER TAKEN, OR CAME BACK SHORT.  Nothing about the
#     world is being reported; the harness has not run with the field
#     census this section reads.  1E, and the row NAMES the re-run.
#   A BUILD PIECE STANDS IN THE WALK-IN.  That is a BODY in the way, the
#     remedy is demolition or a re-route, and it goes through the shared
#     `_obstructed` predicate on a counted body set - never through a
#     terrain answer, because no cut removes a building.
def _tag_ends_family(nums):
    # THE INSTRUMENT ARM AND THE WORLD ARM UNDER ONE CHECK NAME, SPLIT ON
    # THE ROW'S OWN NUMBERS.  `declared`/`parsed` and `tag_rows`/`census`
    # are published only by the two arms that refuse the CENSUS; the arms
    # that refuse the WORLD publish `ends` or `ends_on_tag`.
    if nums.get("declared") is not None or nums.get("tag_rows") is not None:
        return ("CENSUS_NOT_TAKEN",
                "re-run the harness field census for portal_wood:tag; this "
                "row is about the READING, not about the world, and no "
                "verdict on pairing is possible from a partial read")
    return ("RETIRE_AND_RESPAWN",
            "settlements/portal_hall.py retires the arch and re-places it "
            "carrying the tag it should have; it writes no terrain and the "
            "arch is replaced, not removed")


def _approach_family(nums):
    # A DECLARATION MAY PUBLISH THE POSITIVE MEASUREMENT ITS FAMILY NEEDS,
    # and this is the one place that can: `_obstructed` reads an int body
    # count and only this section knows that its own `blockers` list is
    # that count.  None stays None when the arm never ran - unread is not
    # clear, on this instrument too.
    bl = nums.get("blockers")
    if not (isinstance(bl, (list, tuple)) and bl):
        # NO COUNTED BODY IS NOT AN OBSTRUCTION - AND IT IS NOT A HARD ROW
        # THIS SECTION CAN WRITE EITHER.  `check_approach` refuses hard only
        # with a BUILD-PIECE blocker in `blockers`; one with none is this
        # section's source contradicting itself, which no run of any tool
        # changes.  Claiming 1D off a list nobody filled would be a class
        # asserted from an absence; UNKNOWN (`NOT_MEASURED`, until drive 2)
        # would be no class at all.
        return ("SOURCE_CHANGE",
                "a hard approach_ray refusal named no blocker it could "
                "count; check_approach only refuses hard with a build-piece "
                "blocker, so closing this is a change to this section's "
                "source")
    nums["obstruction_bodies"] = len(bl)
    return ("BODY_IN_THE_WAY",
            f"{len(bl)} build piece(s) overlap the walker on every "
            f"approach to this arch; the remedy is to retire them or to "
            f"re-site the arch, and no terrain cut removes a building")


TERMINATION_FAMILIES = {
    "instrument": (
        "CENSUS_NOT_TAKEN",
        "re-run the harness with ZDO_STRINGS=\"portal_wood:tag,sign:text,"
        "sign_notext:text\" and ZDO_FIELDS=<cache>/<fp>/zdostr.tsv; with no "
        "string census every question in this section is UNMEASURED"),
    "tag_ends": _tag_ends_family,
    "tag_verbatim": (
        "RETIRE_AND_RESPAWN",
        "settlements/portal_hall.py retires the arch and re-places it with "
        "the tag the ledger records, or records the replace that already "
        "happened; no terrain is written"),
    "guidepost_bind": (
        "RETIRE_AND_RESPAWN",
        "the board is retired and re-placed against the arch it names; a "
        "sign carries no terrain and nothing else moves"),
    "approach_ray": _approach_family,
}

# ---------------------------------------------------------------------------
# constants, each with where it came from
# ---------------------------------------------------------------------------
PORTAL_PREFAB = "portal_wood"
BOARD_PREFABS = ("sign", "sign_notext")
TAG_KEY = "tag"
TEXT_KEY = "text"

# How far out along an arch's facing axis a walker is still "approaching it".
# MEASURED, not chosen: the four 45-degree bays in the portal hall stood
# their boards 4.395 m out on the facing axis and were still in the way.  A
# span equal to the 3.000 m corridor depth is exactly the rule that cleared
# them, so the span is the corridor depth DOUBLED, which covers the measured
# case with room over it.  Reported on every finding so the number can be
# argued with.
APPROACH_SPAN_M = 6.0
# Where the sweep starts: outside the arch's own solid.  portal_wood's
# collider spans local z -0.4418..0.737 (piece_geometry.json), so 0.75 m
# clears its own frame and nothing else.
APPROACH_START_M = 0.75
APPROACH_STEP_M = 0.25

# A board whose text names a tag-shaped token that no live portal carries.
# The pattern is the project's own tag grammar (`u-<site>`, `x-<crossing>`),
# and a token matching it that names nothing is a sign pointing at an arch
# that is not there.
TAG_TOKEN_RE = re.compile(r"\b[a-z]-[a-z0-9][a-z0-9-]{1,23}\b")


# ---------------------------------------------------------------------------
# the ZDO string payload, decoded rather than trusted
# ---------------------------------------------------------------------------
def decode_zdo_strings(data_b64: str) -> dict[int, str]:
    """`data=` payload -> {key stable-hash: value}.

    The layout is ZDO's own string section and it is decoded rather than
    regex'd out of the base64: uint32 flags, one byte of entry count, then
    per entry an int32 key hash and a 7-bit varint length before the UTF-8.
    Decoding it is what makes "the tag survived the replace" a MEASUREMENT
    of the payload instead of a claim about it.
    """
    raw = base64.b64decode(data_b64)
    if len(raw) < 6:
        return {}
    off = 4                                   # uint32 flags; 0x10 = strings
    count = raw[off]
    off += 1
    out: dict[int, str] = {}
    for _ in range(count):
        if off + 4 > len(raw):
            break
        key = struct.unpack_from("<i", raw, off)[0]
        off += 4
        length = shift = 0
        while off < len(raw):
            b = raw[off]
            off += 1
            length |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        out[key] = raw[off:off + length].decode("utf-8", "replace")
        off += length
    return out


def _live_records(ledger_path) -> tuple[list[dict], dict]:
    """Every ledger record a later `retire` did NOT remove, plus the index
    of retired seqs.  The ledger supplies POPULATION and INTENT here and
    nothing else: not one tag in the verdict comes from it, they are all
    read off the world and only COMPARED with it."""
    recs = [json.loads(line) for line in
            ledger_path.read_text().splitlines() if line.strip()]
    gone: set[int] = set()
    for r in recs:
        if r.get("op") == "retire":
            gone.update(int(s) for s in (r.get("params", {})
                                         .get("retires") or []))
    return [r for r in recs if r.get("seq") not in gone], gone


# ---------------------------------------------------------------------------
# CHECK 1 - two ends per tag, world-wide
# ---------------------------------------------------------------------------
def check_tag_ends(rows: list[tuple], census_count: int,
                   declared: dict) -> list[Finding]:
    out: list[Finding] = []
    # THE INSTRUMENT IS CHECKED BEFORE THE WORLD IS.  "declared 308, returned
    # 41" is a real failure of this project's instruments; a tag census
    # missing half the arches would report a clean world.
    if declared.get("truncated"):
        out.append(Finding(
            check="tag_ends", ok=False,
            message=(f"the tag census is TRUNCATED: the harness declared "
                     f"{declared.get('declared_zdos')} portal_wood ZDOs and "
                     f"{declared.get('rows', len(rows))} rows were parsed. "
                     "No verdict on pairing is possible from a partial read."),
            numbers=dict(declared=declared.get("declared_zdos"),
                         parsed=len(rows))))
    if len(rows) != census_count:
        out.append(Finding(
            check="tag_ends", ok=False,
            message=(f"the tag census covers {len(rows)} portal_wood ZDOs "
                     f"but the object census holds {census_count}. The two "
                     "instruments disagree about how many arches exist."),
            numbers=dict(tag_rows=len(rows), census=census_count)))

    by_tag: dict[str, list] = {}
    for x, y, z, val in rows:
        tag = (val or "").strip()
        if not tag:
            out.append(Finding(
                check="tag_ends", ok=False,
                message=("portal at ({:.2f}, {:.2f}, {:.2f}) has a BLANK "
                         "tag: it joins the world's blank-tag pool and pairs "
                         "at random with a mod-location portal"
                         ).format(x, y, z),
                where=(x, y, z), numbers=dict(ends_on_tag=0)))
            continue
        by_tag.setdefault(tag, []).append((x, y, z))
    for tag, ends in sorted(by_tag.items()):
        if len(ends) == 2:
            continue
        where = ends[0]
        out.append(Finding(
            check="tag_ends", ok=False,
            message=("portal tag {!r} has {} live end(s), not 2, at {}. "
                     "{}".format(
                         tag, len(ends),
                         ", ".join("({:.1f}, {:.1f})".format(e[0], e[2])
                                   for e in ends[:4]),
                         "A one-ended tag pairs at random with the world's "
                         "blank-tag portals - the operator's one-way trip."
                         if len(ends) < 2 else
                         "A third end makes the destination a coin toss.")),
            where=(where[0], where[1], where[2]),
            measured=float(len(ends)), limit=2.0,
            numbers=dict(ends=len(ends),
                         positions=[(round(e[0], 2), round(e[2], 2))
                                    for e in ends])))
    return out


# ---------------------------------------------------------------------------
# CHECK 2 - the tag survived the retire-and-replace, verbatim
# ---------------------------------------------------------------------------
def check_tag_verbatim(live_recs: list[dict], world: list[tuple],
                       tag_hash: int, tol: float = 1.0) -> list[Finding]:
    out: list[Finding] = []
    claimed = 0
    for r in live_recs:
        p = r.get("params") or {}
        if r.get("op") != "portal":
            continue
        claimed += 1
        pos = p.get("pos") or [0.0, 0.0, 0.0]
        want = (p.get("tag") or "").strip()
        payload = decode_zdo_strings(p["data_b64"]) if p.get("data_b64") else {}
        in_payload = payload.get(tag_hash)
        near = [(x, y, z, v) for (x, y, z, v) in world
                if math.hypot(x - pos[0], z - pos[2]) <= tol]
        if p.get("data_b64") and in_payload != want:
            out.append(Finding(
                check="tag_verbatim", ok=False,
                message=(f"ledger seq {r.get('seq')} claims tag {want!r} but "
                         f"its own data_b64 payload carries "
                         f"{in_payload!r}: the payload is what the replace "
                         "actually sends, so the arch would come back with "
                         "the payload's tag"),
                where=(pos[0], pos[1], pos[2]),
                numbers=dict(seq=r.get("seq"), claimed=want,
                             in_payload=in_payload)))
        if not near:
            out.append(Finding(
                check="tag_verbatim", ok=False,
                message=(f"ledger seq {r.get('seq')} records a live portal "
                         f"{want!r} at ({pos[0]:.2f}, {pos[2]:.2f}) and the "
                         "save holds NO portal within "
                         f"{tol:.1f} m of it: the arch is gone and the tag "
                         "it carried is now one-ended"),
                where=(pos[0], pos[1], pos[2]),
                numbers=dict(seq=r.get("seq"), tag=want, search_r_m=tol)))
            continue
        got = near[0][3].strip()
        if got != want:
            out.append(Finding(
                check="tag_verbatim", ok=False,
                message=(f"the arch at ({pos[0]:.2f}, {pos[2]:.2f}) reads "
                         f"tag {got!r}; ledger seq {r.get('seq')} wrote "
                         f"{want!r}. A tag that did not survive its "
                         "retire-and-replace is a portal nobody can reach."),
                where=(pos[0], pos[1], pos[2]),
                numbers=dict(seq=r.get("seq"), world=got, ledger=want)))
    # An arch the ledger never claimed is not automatically wrong - the world
    # has its own mod-location portals - but one carrying OUR tag grammar is.
    claimed_xz = [((p.get("params") or {}).get("pos") or [0, 0, 0])
                  for p in live_recs if p.get("op") == "portal"]
    for (x, y, z, v) in world:
        if any(math.hypot(x - c[0], z - c[2]) <= tol for c in claimed_xz):
            continue
        if TAG_TOKEN_RE.fullmatch((v or "").strip()):
            out.append(Finding(
                check="tag_verbatim", ok=False,
                message=(f"an arch at ({x:.2f}, {z:.2f}) carries tag {v!r} - "
                         "this project's own tag grammar - and NO live "
                         "ledger record claims it. Either a replace was "
                         "never recorded or a retired record is still "
                         "standing."),
                where=(x, y, z), numbers=dict(tag=v)))
    return out


# ---------------------------------------------------------------------------
# CHECK 3 - every board nearest the arch it NAMES
# ---------------------------------------------------------------------------
def bind_by_text(text: str, live_tags: set[str],
                 text_to_tag: dict[str, set[str]]) -> tuple[str | None, str]:
    """Which arch does this board NAME?  Never which arch is nearest."""
    hits = sorted((t for t in live_tags if t in text), key=len, reverse=True)
    if hits:
        return hits[0], "literal-token"
    claimed = text_to_tag.get(text.strip())
    if claimed and len(claimed) == 1:
        return next(iter(claimed)), "ledger-record"
    if claimed:
        return None, "ambiguous:" + ",".join(sorted(claimed))
    dead = TAG_TOKEN_RE.findall(text)
    if dead:
        return dead[0], "dead-token"
    return None, "names-no-portal"


def check_guideposts(boards: list[tuple], world: list[tuple],
                     text_to_tag: dict[str, set[str]]) -> tuple[list[Finding],
                                                                dict]:
    live_tags = {(v or "").strip() for (_x, _y, _z, v) in world if v.strip()}
    out: list[Finding] = []
    pop = dict(boards=len(boards), guideposts=0, by_literal=0, by_record=0,
               names_no_portal=0)
    for (x, y, z, text) in boards:
        if not (text or "").strip():
            pop["names_no_portal"] += 1
            continue
        tag, how = bind_by_text(text, live_tags, text_to_tag)
        if tag is None:
            pop["names_no_portal"] += 1
            if how.startswith("ambiguous"):
                out.append(Finding(
                    check="guidepost_bind", ok=False,
                    message=(f"board at ({x:.2f}, {z:.2f}) reading "
                             f"{text.splitlines()[0]!r} is claimed by two "
                             f"different tags in the record ({how}); nothing "
                             "can say which arch it names"),
                    where=(x, y, z), numbers=dict(how=how)))
            continue
        pop["guideposts"] += 1
        pop["by_literal" if how == "literal-token" else "by_record"] += 1
        ends = [(ex, ey, ez) for (ex, ey, ez, v) in world
                if (v or "").strip() == tag]
        if not ends:
            out.append(Finding(
                check="guidepost_bind", ok=False,
                message=(f"board at ({x:.2f}, {z:.2f}) reading "
                         f"{text.splitlines()[0]!r} names tag {tag!r}, and "
                         "NO live portal in the world carries that tag: the "
                         "board points at an arch that is not there"),
                where=(x, y, z), numbers=dict(tag=tag, live_ends=0,
                                              bound_by=how)))
            continue
        own = min(math.dist((x, y, z), e) for e in ends)
        near_tag, near_d = None, math.inf
        for (ex, ey, ez, v) in world:
            d = math.dist((x, y, z), (ex, ey, ez))
            if d < near_d:
                near_tag, near_d = (v or "").strip(), d
        if near_tag != tag:
            out.append(Finding(
                check="guidepost_bind", ok=False,
                message=(f"board at ({x:.2f}, {z:.2f}) NAMES {tag!r} "
                         f"{own:.3f} m away but stands {near_d:.3f} m from "
                         f"the {near_tag!r} arch: a walker reads it at the "
                         "wrong door"),
                where=(x, y, z), measured=near_d, limit=own,
                numbers=dict(names=tag, own_arch_m=round(own, 3),
                             nearest=near_tag, nearest_m=round(near_d, 3),
                             bound_by=how)))
    return out, pop


# ---------------------------------------------------------------------------
# CHECK 4 - the walk-in
# ---------------------------------------------------------------------------
def check_approach(arches, grid, walker_r: float, walker_h: float,
                   tags: dict, build_pieces: set | None = None
                   ) -> list[Finding]:
    """THE PRECISION SPLIT IS PART OF THE CHECK, not a softening of it.

    `piece_geometry.json` records a BoxCollider by its own shape parameters
    - tight - and a MeshCollider only by `sharedMesh.bounds`, an
    axis-aligned box round an irregular thing.  `rock4_forest` comes out as
    a 27.7 x 24.2 m slab and a Beech1's box is its canopy, not its trunk.
    So a BUILD PIECE on the ray is a REFUSAL - that geometry is measured to
    the centimetre and a board is a board - while a mesh-collider object is
    reported ADVISORY, with its precision named, because failing a world on
    a canopy box would be a number without its precision and the next
    builder would learn to bypass the gate.  Both are printed; only one
    decides.
    """
    build_pieces = build_pieces or set()
    out: list[Finding] = []
    for s in arches:
        surf = s.pos[1]        # portal_wood's collider ymin is -0.007: its
        # own pivot IS its foot, measured in piece_geometry.json, so the arch
        # stands on `pos.y` and no ground batch is needed to say so.
        best_reach, faces, face_hits = -1.0, {}, {}
        for sign, label in ((1, "+Z"), (-1, "-Z")):
            hits: list = []
            reach, worst = A.approach_reach(
                s, grid, surf, walker_r, walker_h, sign,
                APPROACH_SPAN_M, APPROACH_START_M, APPROACH_STEP_M,
                hits=hits)
            faces[label] = (reach, worst)
            face_hits[label] = hits
            best_reach = max(best_reach, reach)
        if best_reach >= APPROACH_SPAN_M - 1e-9:
            continue
        tag = tags.get((round(s.pos[0], 2), round(s.pos[2], 2)), "?")
        named, blockers, piece_blocker = [], [], False
        for label, (reach, worst) in faces.items():
            if worst:
                blockers.append(worst[1].name)
                if worst[1].name in build_pieces:
                    piece_blocker = True
                named.append(f"{label} stopped at {reach:.2f} m by "
                             f"{worst[1].name} overlapping the walker by "
                             f"{-worst[0]:.3f} m at {worst[2]:.2f} m out")
            else:
                named.append(f"{label} clear to {reach:.2f} m")
        tail = ("" if piece_blocker else
                " ADVISORY: every blocker here is a mesh-collider object "
                "recorded only as its bounding box (a Beech1's box is its "
                "canopy, not its trunk), so the overlap is an upper bound "
                "and this finding is reported, not counted.")
        # EVERY PIECE ON EACH FACE'S RAY, NAMED BY PREFAB AND PIVOT, so
        # whose they are can be read off the ledger.  ONE FACE CLEARED IS
        # AN APPROACH, so each face is an alternative: the refusal is the
        # factory's own work when every piece on EITHER face is ours.
        # MEASURED on drive 3: 15 arches refused off `sign` and
        # `portal_wood` - our own boards and neighbouring arches.
        pieces: list[dict] = []
        alts: list[list[int]] = []
        for label in ("+Z", "-Z"):
            idx = []
            for h in face_hits.get(label) or ():
                ref = T.piece_ref(h)
                if ref not in pieces:
                    pieces.append(ref)
                idx.append(pieces.index(ref))
            alts.append(idx)
        out.append(Finding(
            check="approach_ray", ok=False, advisory=not piece_blocker,
            message=(f"arch {tag!r} at ({s.pos[0]:.2f}, {s.pos[2]:.2f}) has "
                     f"no clear walk-in on either face within "
                     f"{APPROACH_SPAN_M:.1f} m: " + "; ".join(named) + tail),
            where=(s.pos[0], s.pos[1], s.pos[2]),
            measured=best_reach, limit=APPROACH_SPAN_M,
            numbers=dict(tag=tag, best_reach_m=round(best_reach, 3),
                         blockers=blockers, build_piece_blocker=piece_blocker,
                         blocker_pieces=pieces, blocker_alternatives=alts,
                         walker_radius_m=walker_r,
                         walker_height_m=walker_h)))
    return out


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def run(ctx) -> list[Finding]:
    I = A.instruments()
    findings: list[Finding] = []

    st = ctx.strings
    rows = st.by_key.get((PORTAL_PREFAB, TAG_KEY), [])
    boards = []
    for pf in BOARD_PREFABS:
        boards += st.by_key.get((pf, TEXT_KEY), [])
    if not st.available or not rows:
        # REFUSE, LOUDLY.  Every other check in this section is a question
        # about strings; with no string census the honest answer is "this
        # world is unmeasured", never "this world is fine".
        return [Finding(
            check="instrument", ok=False,
            message=("no ZDO FIELD census for this save: portal tags and "
                     "board text are UNREAD, so pairing, tag survival and "
                     "guidepost binding are all unmeasured. Re-run the "
                     "harness with ZDO_STRINGS=\"portal_wood:tag,sign:text,"
                     "sign_notext:text\" and ZDO_FIELDS=<cache>/<fp>/"
                     "zdostr.tsv."),
            numbers=dict(portal_tag_rows=len(rows), board_rows=len(boards)))]

    census_portals = [r for r in ctx.census.rows if r[0] == PORTAL_PREFAB]
    findings += check_tag_ends(rows, len(census_portals),
                               st.population(PORTAL_PREFAB, TAG_KEY))

    live_recs, _gone = _live_records(ctx.ledger)
    findings += check_tag_verbatim(live_recs, rows, I._stable_hash(TAG_KEY))

    text_to_tag: dict[str, set[str]] = {}
    for r in live_recs:
        p = r.get("params") or {}
        text = ((p.get("zdo_strings") or {}).get(TEXT_KEY) or "").strip()
        tag = ((r.get("meta") or {}).get("tag") or "").strip()
        if text and tag:
            text_to_tag.setdefault(text, set()).add(tag)
    gp, pop = check_guideposts(boards, rows, text_to_tag)
    findings += gp

    walker_r, walker_h = ctx.walker
    arches = [s for s in ctx.solids_all() if s.name == PORTAL_PREFAB]
    grid = ctx.grid_all()
    tags = {(round(x, 2), round(z, 2)): v for (x, _y, z, v) in rows}
    findings += check_approach(arches, grid, walker_r, walker_h, tags,
                               ctx.build_pieces)

    # The population line, printed whether or not anything failed, because a
    # count without its population is how "184 objects left standing"
    # answered a question about structures.
    findings.append(Finding(
        check="population", ok=True,
        message=("{} portal_wood ZDOs in the save, {} carrying a non-empty "
                 "tag, {} blank, over {} distinct tags; {} sign/sign_notext "
                 "boards of which {} NAME a portal ({} by a literal tag "
                 "token, {} by the ledger's record of their own text) and "
                 "{} name none".format(
                     len(rows), sum(1 for r in rows if r[3].strip()),
                     sum(1 for r in rows if not r[3].strip()),
                     len({r[3].strip() for r in rows if r[3].strip()}),
                     pop["boards"], pop["guideposts"], pop["by_literal"],
                     pop["by_record"], pop["names_no_portal"])),
        numbers=dict(portal_zdos=len(rows), tags=len(
            {r[3].strip() for r in rows if r[3].strip()}), **pop)))
    # AND EVERY REFUSAL ABOVE GETS ITS CLASS, THROUGH THE ONE CLASSIFIER -
    # asked, for every blocker it names, whether this world's ledger put it
    # there.
    return findings + T.classify(sys.modules[__name__], findings, "portals",
                                 placements=T.Placements.read(ctx.ledger))


# ---------------------------------------------------------------------------
# THE SELFTEST - every check in both directions, no world and no server
# ---------------------------------------------------------------------------
# Every failing case below is a state THIS WORLD WAS ACTUALLY IN, taken from
# its own ledger, not an invented one.
# ---------------------------------------------------------------------------
def _yaw(deg: float):
    h = math.radians(deg) / 2.0
    return (0.0, math.sin(h), 0.0, math.cos(h))


def _arch(x: float, z: float, yaw_deg_: float, y: float = 37.1):
    I = A.instruments()
    return I.mk_solid("portal_wood", (x, y, z), _yaw(yaw_deg_),
                      I.load_geometry()[0]["portal_wood"])


def _piece(name: str, x: float, y: float, z: float, yaw_deg_: float = 0.0):
    I = A.instruments()
    return I.mk_solid(name, (x, y, z), _yaw(yaw_deg_),
                      I.load_geometry()[0].get(name) or [])


def _floor_under(x: float, z: float, y: float = 37.1):
    return _piece("stone_floor_2x2", x, y - 0.5, z)


# -- case states ------------------------------------------------------------
def _case_one_ended():
    """seq-for-seq the shape of the defect the ledger's own close report
    exists for: two agents, one tag, one end placed."""
    rows = [(-282.5, 35.6, 225.5, "u-stenvik"),
            (474.1, 47.0, 926.4, "u-stenvik"),
            (-273.6, 37.1, 228.7, "u-wtpeak")]          # the tower end never came
    return check_tag_ends(rows, 3, {})


def _case_two_ended():
    rows = [(-282.5, 35.6, 225.5, "u-stenvik"),
            (474.1, 47.0, 926.4, "u-stenvik")]
    return check_tag_ends(rows, 2, {})


def _case_blank_tag():
    """The anonymous arch: same prefab, same place, no tag.  A census that
    counts arches reports this world as complete."""
    rows = [(-282.5, 35.6, 225.5, "u-stenvik"),
            (474.1, 47.0, 926.4, "")]
    return check_tag_ends(rows, 2, {})


def _case_truncated_census():
    """The instrument, not the world: 30 arches declared, 2 rows parsed."""
    rows = [(-282.5, 35.6, 225.5, "u-stenvik"),
            (474.1, 47.0, 926.4, "u-stenvik")]
    return check_tag_ends(rows, 30, dict(truncated=True, declared_zdos=30,
                                         rows=2))


# u-wtpeak's real re-placement, seq 549 -> the PeakFix replacement, whose
# meta records `data_b64_verbatim: true`.  The payload is this world's own.
_WTPEAK_B64 = "EAAAAAHqkXwpCHUtd3RwZWFr"


def _case_tag_survived():
    I = A.instruments()
    rec = [dict(op="portal", seq=549,
                params=dict(tag="u-wtpeak", pos=[1430.0, 97.0, 1538.0],
                            data_b64=_WTPEAK_B64))]
    world = [(1430.0, 97.0, 1538.0, "u-wtpeak")]
    return check_tag_verbatim(rec, world, I._stable_hash(TAG_KEY))


def _case_tag_died_in_replace():
    """The replace that forgets the payload: the arch comes back with the
    same prefab, the same position and the same rotation, and NO tag.  Count,
    position and rotation all still agree."""
    I = A.instruments()
    rec = [dict(op="portal", seq=549,
                params=dict(tag="u-wtpeak", pos=[1430.0, 97.0, 1538.0],
                            data_b64=_WTPEAK_B64))]
    world = [(1430.0, 97.0, 1538.0, "")]
    return check_tag_verbatim(rec, world, I._stable_hash(TAG_KEY))


def _case_board_names_its_arch():
    """The live hall: the Stenvik board sits on its own arch's lintel, at
    the arch's own xz, so the nearest arch IS the one it names."""
    world = [(-273.66, 37.1, 228.73, "u-stenvik"),
             (-269.20, 37.1, 225.72, "u-vestvik")]
    boards = [(-273.66, 40.5, 228.73, "Stenvik\nNE 1036 m")]
    return check_guideposts(boards, world,
                            {"Stenvik\nNE 1036 m": {"u-stenvik"}})[0]


def _case_board_nearer_wrong_arch():
    """16 of 17 boards were once nearer the WRONG arch.  The board names
    Stenvik and stands 1.1 m from the Vestvik arch and 4.6 m from its own.
    A distance bind calls this correct; a text bind cannot."""
    world = [(-273.66, 37.1, 228.73, "u-stenvik"),
             (-269.20, 37.1, 225.72, "u-vestvik")]
    boards = [(-270.10, 38.5, 226.30, "Stenvik\nNE 1036 m")]
    return check_guideposts(boards, world,
                            {"Stenvik\nNE 1036 m": {"u-stenvik"}})[0]


def _case_board_names_dead_tag():
    """Live on Ulfsland: four boards name tags no arch carries any more."""
    world = [(-273.66, 37.1, 228.73, "u-stenvik")]
    boards = [(-612.0, 34.0, -232.1, "Tree Near u-treenear")]
    return check_guideposts(boards, world, {})[0]


def _hall_bay(with_posts: bool):
    """One bay of the portal hall, from the world's own numbers: the arch at
    its measured position and yaw, the bay's back wall behind it, and - in
    the prior state - the guidepost POST the ledger retired, at the position
    the retire record names."""
    arch = _arch(-273.6617, 228.7303, -157.5)
    out = [arch]
    # The bay's back wall, 0.9 m behind the arch on its -Z face: this is
    # what makes a bay a bay, and it is the reason ONE clear face is the
    # test rather than two.  In the live hall this piece is an
    # `ashwood_decowall_divider` and the -Z sweep stops on it at 0.75 m.
    # The wall is seated ON the floor (centre 0.5 m up, so it spans
    # 37.100..38.100) - centred on the floor plane it would be half buried
    # and a 0.5 m stub is a step the walker steps over, not a wall.
    back = -157.5
    bx = -273.6617 - 0.9 * math.sin(math.radians(back))
    bz = 228.7303 - 0.9 * math.cos(math.radians(back))
    out.append(_piece("stone_wall_2x1", bx, 37.6, bz, back))
    if with_posts:
        # seq 3122, verbatim: the post that carried u-stenvik's board
        out.append(_piece("wood_pole_log", -274.6184, 37.95, 226.4206))
        out.append(_piece("sign", -274.8459, 38.55, 225.8714))
    else:
        # the lintel mount: same xz as the arch, 3.4 m up, over a walker's
        # head by the walker's own measured height
        out.append(_piece("sign", -273.6617, 40.5, 228.7303))
    return out, arch


def _bp():
    """The geometry corpus's own BUILD PIECE set - the half whose colliders
    are box parameters rather than mesh bounds."""
    return A.instruments().load_geometry()[1]


def _case_approach_clear():
    I = A.instruments()
    pieces, arch = _hall_bay(with_posts=False)
    grid = I.Grid(pieces, cell=4.0)
    return check_approach([arch], grid, 0.49, 1.85, {}, _bp())


def _case_approach_blocked_by_post():
    I = A.instruments()
    pieces, arch = _hall_bay(with_posts=True)
    grid = I.Grid(pieces, cell=4.0)
    return check_approach([arch], grid, 0.49, 1.85, {}, _bp())


def _case_approach_tree_is_advisory():
    """LIVE ON ULFSLAND and the reason the precision split exists: the
    u-wtnorth site arch stands in beech woodland and the sweep reports a
    10.524 m overlap - which is the tree's CANOPY BOUNDING BOX, because a
    MeshCollider is recorded in the corpus only as `sharedMesh.bounds`.
    The finding must still be PRINTED, and it must NOT fail the world: a
    number without its precision is how this project got here.  The case
    asserts the check produces a finding AND that the finding is advisory.
    """
    I = A.instruments()
    arch = _arch(152.0, 1197.0, 0.0, y=32.6)
    tree = _piece("Beech1", 152.0, 32.6, 1200.0)
    tree2 = _piece("Beech1", 152.0, 32.6, 1194.0)
    grid = I.Grid([arch, tree, tree2], cell=4.0)
    fs = check_approach([arch], grid, 0.49, 1.85, {}, _bp())
    assert fs and all(f.advisory for f in fs), (
        "a mesh-collider blocker must be reported and must be advisory")
    return fs


def _case_approach_board_outside_the_volume():
    """THE CASE THE OLD CHECK COULD NOT FAIL.  The four 45-degree bays stood
    their boards 4.395 m out along the facing axis - OUTSIDE the 3.000 m
    corridor volume, which they cleared by 0.150 m - and the walk-in still
    went through them.  The volume test accepts this state; the ray refuses
    it, and the case asserts both."""
    I = A.instruments()
    pieces, arch = _hall_bay(with_posts=False)
    yaw = -157.5
    d = 4.395
    px = -273.6617 + d * math.sin(math.radians(yaw))
    pz = 228.7303 + d * math.cos(math.radians(yaw))
    pieces = pieces + [_piece("wood_pole_log", px, 37.95, pz),
                       _piece("sign", px, 38.55, pz)]
    # the OLD test, restated here so its blindness is on the record: is the
    # board inside a 2.94 x 3.00 x 2.00 m corridor volume at the mouth?
    inside_volume = d <= 3.0
    assert not inside_volume, (
        "the board at 4.395 m is OUTSIDE the 3.000 m corridor volume - that "
        "is exactly why the volume test cleared it")
    grid = I.Grid(pieces, cell=4.0)
    return check_approach([arch], grid, 0.49, 1.85, {}, _bp())


def _board_chain(prefabs):
    """A chain whose step-5 settlement build placed the named pieces of the
    bay at their own pivots - the wire is z,x,y, as the game parses it."""
    pieces, _arch_ = _hall_bay(with_posts=True)
    cmds = [f"spawn_object {s.name} pos={s.pos[2]},{s.pos[0]},{s.pos[1]}"
            for s in pieces if s.name in prefabs]
    return T.Placements([dict(
        seq=2311, op="spawn", actor="fixture",
        params=dict(role="waypoint_sign", site_id="stenvik",
                    command_count=len(cmds)),
        meta=dict(build_step="5", build_unit="stenvik"),
        wire=["; ".join(cmds)])], "fixture: the bay's board chain")


def _approach_owned_by(chain):
    I = A.instruments()
    pieces, arch = _hall_bay(with_posts=True)
    fs = check_approach([arch], I.Grid(pieces, cell=4.0), 0.49, 1.85, {},
                        _bp())
    T.classify(sys.modules[__name__], fs, "portals", placements=chain)
    return fs[0].numbers


def _case_our_own_board_in_the_bay_is_factory_work():
    """A FACE WHOSE EVERY BLOCKER THE LEDGER PLACED IS THE FACTORY'S WORK.
    MEASURED on drive 3: 15 arches refused off `sign` and `portal_wood` -
    our own boards and neighbouring arches - classed 1D as if a player had
    built them.  One face cleared is an approach, so the +Z face (post and
    board, both ours) decides it while the -Z back wall is nobody's."""
    n = _approach_owned_by(_board_chain({"wood_pole_log", "sign"}))
    steps = " ".join(n.get("owner_step") or ())
    return [Finding(
        check="termination", ok=n.get("termination_class") == "ACTIONABLE"
        and "bodies" in steps,
        message=(f"our own post and board on the +Z face classed "
                 f"{n.get('termination_class')}, owner {n.get('owner_step')}"),
        where=(-273.66, 228.73), numbers=dict(cls=n.get("termination_class")))]


def _case_a_face_with_one_foreign_piece_is_not_ours():
    """THE OTHER DIRECTION: the ledger placed the board but not the post, so
    no face is wholly ours and the row keeps 1D."""
    n = _approach_owned_by(_board_chain({"sign"}))
    return [Finding(
        check="termination",
        ok=n.get("termination_class") == "1D_OBSTRUCTED"
        and n.get("blocker_ownership") == "foreign",
        message=(f"a face with a post no record placed classed "
                 f"{n.get('termination_class')}"),
        where=(-273.66, 228.73), numbers=dict(cls=n.get("termination_class")))]


CASES = [
    Case("tag_one_ended", True, _case_one_ended,
         prior_state="a tag whose hall end exists and whose tower end does "
                     "not - the two-agent break the ledger's close report "
                     "exists for"),
    Case("tag_two_ended", False, _case_two_ended),
    Case("tag_blank", True, _case_blank_tag,
         prior_state="19 blank-tag portals were once a random-destination "
                     "pool on this world"),
    Case("tag_census_truncated", True, _case_truncated_census,
         prior_state="one -detailed call declared 308 objects and returned "
                     "41 rows"),
    Case("tag_survived_replace", False, _case_tag_survived),
    Case("tag_died_in_replace", True, _case_tag_died_in_replace,
         prior_state="u-wtpeak's tower end was retired and re-placed; the "
                     "payload had to ride through verbatim or the arch "
                     "returns anonymous with count, position and rotation "
                     "all unchanged"),
    Case("board_names_its_arch", False, _case_board_names_its_arch),
    Case("board_nearer_wrong_arch", True, _case_board_nearer_wrong_arch,
         prior_state="16 of 17 guideposts were nearer the wrong arch"),
    Case("board_names_dead_tag", True, _case_board_names_dead_tag,
         prior_state="live on Ulfsland: 'Tree Near u-treenear' names a tag "
                     "no arch carries"),
    Case("approach_clear_lintel", False, _case_approach_clear),
    Case("approach_blocked_by_post", True, _case_approach_blocked_by_post,
         prior_state="seq 3122: the post that carried u-stenvik's board, at "
                     "the position its own retire record names"),
    Case("approach_tree_is_advisory", False, _case_approach_tree_is_advisory,
         prior_state="live on Ulfsland: u-stenvik and u-wtnorth both report "
                     "a blocked ray, and every blocker is a Beech1 bounding "
                     "box"),
    Case("approach_board_outside_volume", True,
         _case_approach_board_outside_the_volume,
         prior_state="the four 45-degree bays: boards 4.395 m out on the "
                     "facing ray, clearing the 3.000 m corridor volume by "
                     "0.150 m, and still in the way"),
    Case("approach_our_own_board_is_factory_work", False,
         _case_our_own_board_in_the_bay_is_factory_work,
         prior_state="drive 3, save 05eb7c6c05de: 15 approach_ray rows "
                     "blocked by our own sign/portal_wood, classed 1D",
         neuter=T.neuter_blocker_ownership("blind"),
         neuters="ownership blind: every blocker an operator's"),
    Case("approach_a_foreign_post_keeps_1d", False,
         _case_a_face_with_one_foreign_piece_is_not_ours,
         prior_state="the same bay with the post placed by no record",
         neuter=T.neuter_blocker_ownership("all_ours"),
         neuters="every named blocker claimed as ours"),
]

register(Section(
    name="portals",
    measures=("every portal tag has exactly two live ends world-wide, every "
              "tag survived its retire-and-replace verbatim, every guidepost "
              "stands nearest the arch its TEXT names, and a walker's own "
              "capsule can reach at least one face of every arch"),
    instrument=("the save's ZDO FIELD census (WaterProof WATERPROOF_ZDO_"
                "STRINGS over ZDOMan.m_objectsByID: portal_wood/tag and "
                "sign/text, read offline from a frozen copy) for the strings; "
                "the object census plus each prefab's own rotated solid for "
                "the geometry; the ledger for POPULATION and INTENT only"),
    population=("every portal_wood ZDO in the save and every sign/sign_notext "
                "ZDO in the save; guideposts are the subset whose text names "
                "a portal tag, counted and reported separately from the "
                "boards that name none"),
    run=run,
    cases=CASES))
