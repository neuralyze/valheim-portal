#!/usr/bin/env python3
"""Signposts: the ONE emitter for every sign this world gets, and the tag registry
the browser map draws its labels from.

WHY THIS FILE EXISTS
====================
The operator regenerated Ulfsland after reporting "i wasnt able to find any portals
or get anywhere", on a world whose portal network was provably correct. It was
correct. MEASURED from the archived pre-wipe save (`worldintel` over
`world-archive-20260916T014115-worldgen/Ulfsland`): 33 `portal_wood` ZDOs, 22 of
them tagged, forming ELEVEN exact pairs, and every pair shared exactly one ZDO
connection hash with connection types 17 (source) and 1 (target) -- so all eleven
were genuinely connected, not dangling. The remaining 11 were the blank-tag portals
that mod world locations bring, which pair among themselves at random.

And the coordinate that settles it: the pre-bonemass character-template spawn is
(-4674, 72, -329), and a CONNECTED portal tagged `u-workshop` stood at
(-4673.50, 70.91, -325.50) -- 3.54 m horizontally and 1.09 m below it. The operator
spawned three and a half metres from a working portal into a hall holding eleven
more and could not find it.

So this is not a distance defect and not a pairing defect. It is LEGIBILITY, and
the measurement that explains it is this:

  * `TeleportWorld::GetHoverText` is the ONLY place in `assembly_valheim.dll`
    (1.0.12) that a portal's tag is ever shown to a player.
  * `Player::FindHoverObject` resolves a hover target only when
    `Vector3.Distance(m_eye.position, hit.point) < m_maxInteractDistance +
    hoverable.GetHoverOffset()`, and `m_maxInteractDistance` is initialised to
    `5.0` in `Player`'s own constructor (`ldc.r4 5.` / `stfld`).

Five metres, with the crosshair on the arch. Past that, a portal is an anonymous
stone ring and its destination does not exist as far as the player is concerned.

WHY A SIGN IS THE FIX, MEASURED RATHER THAN HOPED
=================================================
A sign's text is not hover text. It is a world-space mesh:

  * `Sign::UpdateText` reads ZDO string `ZDOVars.s_text` and
    `OnCheckPermissionCompleted` assigns it to `m_textWidget`, a
    `TMPro.TextMeshProUGUI`. Driven by `InvokeRepeating("UpdateText", 2, 2)` and
    short-circuited on an unchanged `ZDO.DataRevision`.
  * The ENTIRE `Sign` class contains no distance test: no `Vector3.Distance`, no
    `Player::GetClosestPlayer`, no activation range, and it never enables or
    disables the widget. Contrast `TeleportWorld`, which HAS an `m_activationRange`
    field and does call `GetClosestPlayer` -- and spends it on the portal glow,
    never on the text.
  * So the only remaining gate is whether the GameObject exists, which is the zone
    active area: `ZNetScene::PointInsideActiveArea` admits a point within a
    CHEBYSHEV distance of `factor * m_zoneSize` of the viewer's zone, factor 1.5 by
    default, 1.0 at `NearSimulationDistance == 1` and 1.75 at 2 (non-classic). At
    the 64 m zone size the save data agrees with, that is a ~96 m square by
    default -- roughly 19x the 5 m the portal tag needs, with no crosshair and no
    keypress.

NOT MEASURED, and deliberately not claimed: whether a wood sign's TMP glyphs are
legible to a human at 96 m. Font size is a Unity-serialised prefab value that is not
in the IL. What this design rests on is the case that matters -- standing in a hub
hall you are within a few metres of each arch, and at that range a sign reads while
you walk past it.

  * There is no character cap on the way in. MEASURED: `Sign::Interact` calls
    `TextInput::RequestText(this, "$piece_sign_input", m_characterLimit)`, so the cap
    is an INPUT cap on the retype box exactly as `TeleportWorld`'s 10 is. Writing
    the string straight onto the ZDO through `data=` bypasses it. Confirmed live on
    Ulfsland: a 29-character text round-tripped verbatim (see `PROBE` below).
  * No UGC gate, because we never write an author. MEASURED: with ZDO `author`
    empty, `Sign::UpdateText` sets `m_author = Some(PlatformUserID.None())`,
    `UpdateViewPermission` sees `IsValid() == false` and calls
    `OnCheckPermissionCompleted(RelationsManagerPermissionResult.Granted)` -- which
    is literal `int32(0x00000000)` -- so `m_isViewable` becomes true and the raw
    `m_currentText` is assigned with no censor filtering. A sign with an author could
    be hidden behind another player's UGC settings; ours cannot.

THE LIVE PROBE, RUN ONCE AND CLEANED UP
=======================================
Ulfsland, RCON, 2026-09-15. `spawn_object sign pos=2000,2000,200 rot=0,0,0
from=0,0,0 data=<text="WayFinding probe -- delete me">` then
`findObjects -prefab sign -near 2000 200 2000 30 -detailed` returned, verbatim:

  -Prefab: sign Id: 793:-75044325 Position: (2000 200 2000) Zone: (31 31)
   Rotation: (0 0 0) Creator: 0 Health: 100 Support: 1
   Text: WayFinding probe -- delete me (author: )

Four things measured in one line: the prefab name is `sign` and it is spawnable; the
`data=` payload lands on the ZDO; 29 characters survive verbatim, so the 10-char
class of cap does not apply; and `author:` is empty, which is the ungated branch
above. Then `deleteObjects -prefab sign -near 2000 200 2000 30` reported
`Deleted 1/1` and the re-query returned "No objects found matching the provided
criteria". Nothing was left in the world.

`Support: 1` on a board hanging in mid-air is suggestive that `sign` does not carry
`m_noSupportWear`, but it is a value read at spawn before any `UpdateSupport` ran,
so it is NOT taken as proof. `WearNTear::UpdateWear` sets its damage accumulator to
100 when `m_noSupportWear && !HaveSupport()`, and `m_noSupportWear` is a serialised
prefab bool this repo cannot read. So every waypoint here stands on its own POST,
and the question never has to be answered: a `wood_pole_log` planted in the ground
is supported by definition, and the board hangs on it.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import DATA, JUMPSTART

sys.path.insert(0, str(JUMPSTART / "blueprints"))
import base_geometry  # noqa: E402
import data_entry  # noqa: E402
import to_rcon_plan  # noqa: E402


# The board and the post it hangs on. MEASURED from
# tools/jumpstart/blueprints/data/piece_geometry.json (game_version 1.0.12), local
# AABBs in metres, origin at the collider centre:
#   sign           (-0.5, -0.275, -0.0445) .. (0.5, 0.275, 0.0445)   is_support False
#   wood_pole_log  (-0.2, -1.0,   -0.2   ) .. (0.2, 1.0,   0.2   )   is_support True
# The board is a flat panel with no leg of its own, which is why it needs the post.
BOARD_PREFAB = "sign"
POST_PREFAB = "wood_pole_log"

# Only the two that seed a search or set a height are named. Everything else about
# these colliders is read from `piece_geometry.json` at the moment it is needed, so
# a re-measured corpus cannot leave a stale copy of a half-extent in this file.
BOARD_HALF_DEPTH_M = 0.0445
POST_HALF_HEIGHT_M = 1.0
POST_HALF_WIDTH_M = 0.2

# How far the post is driven into the ground. The post's origin is its centre, so
# its foot sits at origin_y - 1.0; sinking it guarantees the foot is inside terrain
# rather than resting on a rounding error, which is what `HaveSupport` turns on.
POST_EMBED_M = 0.15

# The board's own centre height above the ground the post is planted in. The post
# spans ground-0.15 .. ground+1.85, so a board centred at 1.45 has its top at 1.725
# -- on the post, and at the eye height of a standing player rather than at their
# knees or over their head.
BOARD_CENTRE_Y_M = 1.45

# The clearance every waypoint piece keeps from every other solid, in metres.
# `fixtures.audit()` fails a build on ANY overlap, so the requirement is only that
# the gap be positive; this is the margin above that, so a 1 cm arithmetic drift
# somewhere else cannot turn a legal placement into a failed build.
MIN_GAP_M = 0.05

# The seed the offset search starts from, and how far it is allowed to walk. These
# are search bounds and NOT the answer: the answer is solved per yaw against the
# audit's own box builder, because the first cut of this file guessed 2.9 m from
# `portal_wood`'s axis-aligned half-width of 2.14 m and that was WRONG at 45 deg.
#
# Why it was wrong, since it is the instructive part: `fixtures.fixture_box` builds
# the world AABB of the yaw-ROTATED corners, so a 4.23 x 1.18 m arch at yaw 45 has
# an x-extent of |cos45|*2.117 + |sin45|*0.589 = 1.91 m rather than 0.59 m, and a
# post displaced 2.9 m along the arch's diagonal lands 2.05 m out on each world
# axis -- inside it. A verification pass caught it at yaw 45, 225 and 315, which are
# exactly the yaws three of this world's placements use (meadows-starter-hall 225,
# plains-farm-base 315). A constant derived from one orientation is the same defect
# shape as a clearance measured at one location.
PORTAL_SIDE_SEED_M = 2.5
OFFSET_SEARCH_LIMIT_M = 12.0
OFFSET_SEARCH_STEP_M = 0.05

# `RconPacket.MaxPayloadSize` is 4050 and the whole `spawn_object` line has to fit
# inside it. Everything but the text is ~120 bytes, and base64 inflates by 4/3, so a
# text this long cannot overflow the packet even before the fixed part is counted.
# The limit exists to refuse an essay, not to ration characters: the actual design
# limit is how much fits on a 1 m board and stays readable.
SIGN_TEXT_MAX_CHARS = 512

# A line of text wider than this stops being a label. 1 m of board at the game's
# sign font holds roughly this much per line before TMP shrinks it to nothing; the
# emitter refuses rather than silently producing an illegible board.
SIGN_LINE_MAX_CHARS = 24

# TMP parses rich-text markup, and `Sign` assigns the RAW ZDO string to the widget
# (`OnCheckPermissionCompleted` -> `TMP_Text::set_text`) rather than the stripped
# form `GetHoverText` uses. So an unbalanced `<` in a sign's text is swallowed as a
# broken tag and the word vanishes off the board. Refused rather than escaped,
# because a destination name has no business containing one.
_SIGN_TEXT_BAD = re.compile(r"[<>\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class WaypointError(ValueError):
    """A sign that would be placed wrong, or unreadable, or nameless."""


def sign_text_problem(text: str | None) -> str | None:
    """Why this sign text is unusable, or None when it is usable.

    A string and not a bool for the same reason `portal_tag_problem` is: "invalid
    sign text" is not something an operator can act on.
    """
    if text is None:
        return ("no sign text was supplied, so this would place a board reading "
                "whatever the prefab's `m_defaultText` is -- which is exactly as "
                "useless as the unlabelled arch it is meant to explain")
    if not isinstance(text, str):
        return f"sign text must be a string, got {type(text).__name__}"
    if not text.strip():
        return ("the sign text is empty or whitespace, which places a blank board: "
                "the defect, not the fix")
    if text != text.strip():
        return (f"the sign text {text!r} has leading or trailing whitespace, which "
                f"TMP renders as indentation nobody chose")
    if len(text) > SIGN_TEXT_MAX_CHARS:
        return (f"the sign text is {len(text)} characters; the cap here is "
                f"{SIGN_TEXT_MAX_CHARS}, which is a packet-safety bound and not a "
                f"legibility one -- a board this long is unreadable long before it "
                f"is untransmittable")
    bad = _SIGN_TEXT_BAD.search(text)
    if bad:
        return (f"the sign text contains {bad.group(0)!r}. `Sign` assigns the RAW "
                f"ZDO string to a TextMeshPro widget, so TMP parses `<` as rich-text "
                f"markup and eats the rest of the tag; control characters are worse. "
                f"MEASURED: it is `GetHoverText` that calls RemoveRichTextTags, not "
                f"the widget path")
    for line in text.split("\n"):
        if len(line) > SIGN_LINE_MAX_CHARS:
            return (f"the line {line!r} is {len(line)} characters; a 1 m board holds "
                    f"about {SIGN_LINE_MAX_CHARS} before TMP shrinks it past reading. "
                    f"Break it with a newline")
    return None


def sign_data(text: str) -> str:
    """The `data=` payload that puts `text` on a sign at spawn time.

    Deliberately identical in shape to `fixtures.portal_data`: one ZDO string, no
    author, no fabricated identity. `tagauthor`/`author` are left unset because
    writing one would invent a `PlatformUserID` AND would put the board behind the
    viewer's UGC permission check -- see the module docstring for the measured
    branch that keeps an authorless sign visible.
    """
    problem = sign_text_problem(text)
    if problem:
        raise WaypointError(problem)
    entry = data_entry.DataEntry()
    entry.strings[data_entry.stable_hash("text")] = text
    return entry.encode()


@dataclass(frozen=True)
class Piece:
    """One spawnable piece of a waypoint, in WORLD coordinates."""

    prefab: str
    x: float
    y: float
    z: float
    yaw: float
    text: str | None = None

    def spawn_command(self) -> str:
        """The console line that lands this piece headlessly.

        `spawn_object` and not ValheimRcon's own `spawn`, because `spawn` takes no
        data payload and so cannot write a sign's text -- a board placed with it is
        the blank board. Argument orders are `to_rcon_plan`'s, MEASURED from IL:
        `pos=` is z,x,y and `rot=` is euler y,x,z.
        """
        line = (f"spawn_object {self.prefab}"
                f" pos={to_rcon_plan.pos_arg(self.x, self.y, self.z)}"
                f" rot={to_rcon_plan.rot_arg(0.0, self.yaw, 0.0)}"
                f" {to_rcon_plan.FROM_ORIGIN}")
        if self.text is not None:
            line += f" data={sign_data(self.text)}"
        return line

    def audit_row(self) -> tuple[str, float, float, float, float]:
        """The row shape `blueprints/fixtures.audit()` takes.

        A waypoint is never audited here. Intersection with a building's solids is
        `fixtures`' measurement and it already fails a build on any hit; duplicating
        the test with a second, weaker copy of the geometry is how two answers to one
        question get into a repo.
        """
        return (self.prefab, self.x, self.y, self.z, self.yaw)


def _fixtures():
    """`blueprints.fixtures`, imported late.

    It pulls in the whole piece-geometry corpus, and the validators above have to be
    importable without it.
    """
    import fixtures  # noqa: PLC0415

    return fixtures


def _separation(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Metres of clear air between two `fixtures.fixture_box` AABBs.

    Boxes are (xmin, xmax, ymin, ymax, zmin, zmax). Two AABBs are disjoint as soon
    as ONE axis separates them, so the separation is the largest per-axis gap;
    negative means they interpenetrate, which is what `fixtures.audit()` fails on.
    """
    return max(max(a[0], b[0]) - min(a[1], b[1]),
               max(a[2], b[2]) - min(a[3], b[3]),
               max(a[4], b[4]) - min(a[5], b[5]))


def _walk_clear(seed: float, step_dir: tuple[float, float],
                place: Any, obstacles: list[tuple[float, ...]]) -> float:
    """Smallest offset along `step_dir` at which `place(offset)` clears everything.

    `place(offset)` returns the boxes the waypoint would occupy at that offset. The
    search is a walk rather than closed-form arithmetic on purpose: the closed form
    has to reason about a rotated box's world AABB, and the version of this file that
    did that was wrong at 45 degrees. Walking against the audit's OWN box builder
    cannot disagree with the audit.
    """
    offset = seed
    while offset <= OFFSET_SEARCH_LIMIT_M:
        boxes = place(offset)
        if all(_separation(box, obstacle) >= MIN_GAP_M
               for box in boxes for obstacle in obstacles):
            return offset
        offset += OFFSET_SEARCH_STEP_M
    raise WaypointError(
        f"no offset up to {OFFSET_SEARCH_LIMIT_M} m along {step_dir} clears the "
        f"{len(obstacles)} obstacle(s) by {MIN_GAP_M} m; this waypoint has nowhere "
        f"to stand and the caller has to choose another side or another spot")


def _post_and_board(x: float, z: float, ground_y: float, facing_yaw: float,
                    reach: float, text: str | None) -> list[Piece]:
    radians = math.radians(facing_yaw)
    return [
        Piece(POST_PREFAB, x, ground_y - POST_EMBED_M + POST_HALF_HEIGHT_M, z,
              facing_yaw),
        Piece(BOARD_PREFAB, x + math.sin(radians) * reach,
              ground_y + BOARD_CENTRE_Y_M, z + math.cos(radians) * reach,
              facing_yaw, text=text),
    ]


def _boxes(pieces: list[Piece]) -> list[tuple[float, ...]]:
    """World AABBs for these pieces, built by the SAME function `fixtures.audit()` uses.

    `Piece.y` is passed straight through. `fixture_box` adds the solid's own local
    y-extent to the y it is given, so handing it the pivot yields pivot+local, which
    is the piece's real world box. A first cut of this helper "corrected" the pivot
    to a surface first and put the post's foot 1 m ABOVE the ground it was supposed
    to be driven into -- caught by the embed check below, which is the whole reason
    that check exists.
    """
    fx = _fixtures()
    return [fx.fixture_box(p.prefab, p.x, p.y, p.z, p.yaw) for p in pieces]


def guidepost(x: float, z: float, ground_y: float, facing_yaw: float,
              text: str) -> list[Piece]:
    """A free-standing signpost: a post planted at (x, z) and a board facing `yaw`.

    `ground_y` is the walkable surface the post is driven into -- the same datum the
    rest of this tree calls `lowest_major_walkable_surface`. It is a REQUIRED input
    and never guessed: a post placed against an assumed height is the floating-
    vegetation defect in miniature.

    `facing_yaw` is the direction the board's face points, in the repo's
    euler_degrees_yxz convention. A reader stands on that side.

    The board's standoff from the post is SOLVED against `fixtures.fixture_box`
    rather than computed from half-widths, because the audit's box is the yaw-rotated
    world AABB and closed-form half-width arithmetic gets that wrong off the cardinals.
    """
    problem = sign_text_problem(text)
    if problem:
        raise WaypointError(problem)
    post = _post_and_board(x, z, ground_y, facing_yaw, 0.0, None)[0]
    post_box = _boxes([post])[0]
    reach = _walk_clear(
        POST_HALF_WIDTH_M + BOARD_HALF_DEPTH_M, (0.0, 1.0),
        lambda offset: _boxes(
            _post_and_board(x, z, ground_y, facing_yaw, offset, text)[1:]),
        [post_box])
    return _post_and_board(x, z, ground_y, facing_yaw, reach, text)


def portal_guidepost(portal_x: float, portal_y: float, portal_z: float,
                     portal_yaw: float, text: str,
                     side: str = "left") -> list[Piece]:
    """A guidepost beside a portal, naming where that portal GOES.

    The post stands to one side of the arch along the arch's own width axis, and its
    board faces the same way the arch does -- so somebody walking up to the portal
    from the front reads the destination before they reach hover range, which is the
    entire point.

    How far to the side is SOLVED, not declared. The first version of this function
    used 2.9 m, derived from `portal_wood`'s axis-aligned half-width of 2.14 m, and a
    verification pass measured the post INSIDE the arch at yaw 45, 225 and 315 --
    three of the yaws this world's own placements use. The search below walks the post
    outward against `fixtures.fixture_box`, the same builder `fixtures.audit()` uses,
    so the result cannot disagree with the audit that gates the build.

    `portal_y` is the portal's own base Y. A portal stands on the floor
    (`portal_wood`'s AABB starts at y -0.007, so its origin IS its foot), which makes
    the arch's Y and the surface the post is driven into the same surface. That is an
    assumption about the CALLER's placement and not about terrain, and it is why this
    takes the arch's transform rather than a bare coordinate: whoever placed the arch
    knows where its foot is and nobody else does.
    """
    if side not in ("left", "right"):
        raise WaypointError(f"side must be 'left' or 'right', got {side!r}")
    problem = sign_text_problem(text)
    if problem:
        raise WaypointError(problem)
    # The arch's width axis is perpendicular to its facing. yaw is clockwise from
    # +Z, so facing is (sin, cos) and the right-hand side is (cos, -sin).
    radians = math.radians(portal_yaw)
    hand = 1.0 if side == "right" else -1.0
    step = (math.cos(radians) * hand, -math.sin(radians) * hand)
    arch = _fixtures().fixture_box("portal_wood", portal_x, portal_y, portal_z,
                                   portal_yaw)

    def at(offset: float) -> list[tuple[float, ...]]:
        px = portal_x + step[0] * offset
        pz = portal_z + step[1] * offset
        return _boxes(guidepost(px, pz, portal_y, portal_yaw, text))

    offset = _walk_clear(PORTAL_SIDE_SEED_M, step, at, [arch])
    return guidepost(portal_x + step[0] * offset, portal_z + step[1] * offset,
                     portal_y, portal_yaw, text)


def wrap_for_board(text: str) -> str:
    """Break a destination name onto as many lines as a 1 m board needs.

    A board is two-dimensional, so a 25-character name is a WRAPPING problem and not
    an error -- refusing it, which is what the first cut of this file did, would have
    forced somebody to invent an abbreviation for `Mistlands Blackforge Base` and put
    a second, shorter name for one place into the world. One name, more lines.

    A single word longer than a line is still an error: TMP will not break inside a
    word, and silently letting it overflow is how a board ends up reading
    "Mistlands Blackfo".
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        if len(word) > SIGN_LINE_MAX_CHARS:
            raise WaypointError(
                f"the word {word!r} is {len(word)} characters and a 1 m board holds "
                f"about {SIGN_LINE_MAX_CHARS}; TMP does not break inside a word, so "
                f"this needs a shorter name rather than a wrap")
        candidate = f"{current} {word}".strip()
        if len(candidate) > SIGN_LINE_MAX_CHARS:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


# Lines that fit on one board before it stops being readable. INFERRED, not
# measured: the board's collider is 1.0 x 0.55 m (MEASURED from piece_geometry.json)
# but the TMP font size and line spacing are Unity-serialised prefab values that are
# not in the IL, so the number of lines is a judgement and says so. Four is chosen
# to be comfortably inside any plausible value; the design below never needs more,
# which is the real reason it is safe.
SIGN_MAX_LINES = 4


def _board(text: str, what: str) -> str:
    problem = sign_text_problem(text)
    if problem:
        raise WaypointError(f"{what}: {problem}")
    lines = text.split("\n")
    if len(lines) > SIGN_MAX_LINES:
        raise WaypointError(
            f"{what}: {len(lines)} lines on a 1.0 x 0.55 m board. The cap here is "
            f"{SIGN_MAX_LINES} and it is INFERRED rather than measured, so it is "
            f"deliberately conservative -- split this across more boards instead of "
            f"raising it")
    return text


def hall_sign_text(name: str, portal_count: int) -> str:
    """The board at the door of a portal hall.

    NOT a directory of the eleven destinations, and the reason is measured geometry
    rather than taste: the board is 1.0 x 0.55 m, and eleven tag lines on it is 119
    characters on a panel the size of a sheet of A4 seen from across a room. The
    naming is done by one board per arch, where the reader is already standing.
    What this board does is answer the question the operator actually had -- "is
    there a portal hall, and is this it".
    """
    return _board(f"{wrap_for_board(name)}\n{portal_count} portals", "hall board")


COMPASS_POINTS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def bearing_and_range(from_xz: tuple[float, float],
                      to_xz: tuple[float, float]) -> tuple[float, str, float]:
    """Bearing in degrees, its compass point, and the distance, from one xz to another.

    Valheim's world axes: +Z is north and +X is east, and a yaw is degrees clockwise
    from +Z -- the same convention `drawHeading` in the map renderer is checked
    against. So the bearing from a to b is atan2(dx, dz), NOT atan2(dz, dx): getting
    that backwards produces a signpost that is wrong by exactly the amount nobody
    notices until they walk it.
    """
    dx = to_xz[0] - from_xz[0]
    dz = to_xz[1] - from_xz[1]
    bearing = math.degrees(math.atan2(dx, dz)) % 360.0
    index = int((bearing + 11.25) % 360.0 // 22.5)
    return bearing, COMPASS_POINTS[index], math.hypot(dx, dz)


def pointer_text(name: str, compass: str, metres: float) -> str:
    """A board that says where something is and how far, for a player on foot.

    The distance is rounded to 10 m. A signpost claiming 314 m asserts a precision
    that pacing cannot check and that the reader does not want; 310 m is the same
    information without the false confidence.
    """
    rounded = int(round(metres / 10.0) * 10)
    return _board(f"{wrap_for_board(name)}\n{compass} {rounded} m", "pointer board")


# ---------------------------------------------------------------------------
# The registry: tag -> destination, and the labels the browser map draws.
# ---------------------------------------------------------------------------

REGISTRY_SCHEMA_VERSION = 1


def _world_dir(world: str) -> Path:
    return JUMPSTART / "worlds" / world


def _placements(world: str) -> list[tuple[str, dict[str, Any]]]:
    """Every solved placement in every preset of `world`, with its preset name."""
    out: list[tuple[str, dict[str, Any]]] = []
    root = _world_dir(world)
    for path in sorted(root.glob("*/placements.yaml")):
        doc = yaml.safe_load(path.read_text())
        for place in doc.get("placements") or []:
            out.append((path.parent.name, place))
    return out


def _display_name(installation_id: str) -> str:
    """A readable destination name from an installation id.

    The id IS the name in this tree -- `iron-era-workshop`, `plains-farm-base` --
    so the derivation is mechanical rather than a second hand-maintained table that
    can disagree with the first. Kept short because it has to fit on a board:
    `SIGN_LINE_MAX_CHARS` is enforced by `sign_text_problem`, and a name that will
    not fit is a build error, not something to silently truncate.
    """
    words = [w for w in installation_id.split("-") if w]
    return " ".join(w.capitalize() for w in words)


@dataclass
class Destination:
    tag: str
    installation_ids: list[str]
    presets: list[str]
    role: str
    display: str
    # `display` is the one-line name the browser map draws. `board` is the same name
    # broken to fit a 1 m sign. They are the same name and must never diverge, which
    # is why one is derived from the other rather than authored twice.
    board: str = ""
    spoke_xz: tuple[float, float] | None = None
    spoke_y: float | None = None
    spoke_yaw: float | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tag": self.tag,
            "display": self.display,
            "board": self.board,
            "installations": self.installation_ids,
            "presets": self.presets,
            "role": self.role,
        }
        if self.spoke_xz is not None:
            out["spoke"] = {"x": self.spoke_xz[0], "z": self.spoke_xz[1],
                            "y": self.spoke_y, "yaw": self.spoke_yaw}
        if self.problems:
            out["problems"] = self.problems
        return out


def build_registry(world: str) -> dict[str, Any]:
    """Derive the tag registry from the placements, which are the source of truth.

    One tag maps to one PAIR of portals and may be claimed by more than one
    installation id: `sandbox-harbour` declares `duplicate_of: pre-elder#early-dock`
    -- same body, same coordinates, one waterfront -- so two ids legitimately point
    at one tag. That is not a collision. What IS a collision, and is reported as a
    problem, is a third portal carrying an existing tag: MEASURED,
    `Game::FindRandomUnconnectedPortal` pairs on exact string equality and then draws
    `Random.Range(0, count)`, so a third same-tag portal makes the pair
    nondeterministic on every load.
    """
    import fixtures  # noqa: PLC0415  (heavy; only the registry path needs it)

    destinations: dict[str, Destination] = {}
    hub: dict[str, Any] | None = None
    problems: list[str] = []

    for preset, place in _placements(world):
        portal = place.get("portal") or {}
        role = portal.get("role")
        solved = place.get("solved") or {}
        if role == "hub":
            hub = {
                "installation": place["id"],
                "preset": preset,
                "x": solved.get("x"),
                "y": solved.get("y"),
                "z": solved.get("z"),
                "tags": list(portal.get("tags") or []),
                "portal_pieces": portal.get("pieces_to_place"),
            }
            continue
        tag = place.get("portal_tag")
        if not tag:
            continue
        tag_problem = fixtures.portal_tag_problem(tag)
        yaw = (place.get("rotation") or {}).get("yaw")
        existing = destinations.get(tag)
        if existing is None:
            display = _display_name(place["id"])
            entry = Destination(
                tag=tag,
                installation_ids=[place["id"]],
                presets=[preset],
                role=role or "spoke",
                display=display,
                spoke_xz=(solved.get("x"), solved.get("z")),
                spoke_y=solved.get("y"),
                spoke_yaw=yaw,
            )
            if tag_problem:
                entry.problems.append(tag_problem)
            # The board carries the WRAPPED name, so validation has to be against
            # what is actually written rather than against the one-line display the
            # map uses. Checking the unwrapped form reported a 25-character name as
            # unusable when a two-line board holds it comfortably.
            try:
                entry.board = wrap_for_board(display)
            except WaypointError as failure:
                entry.problems.append(
                    f"the derived destination name {display!r} cannot go on a board: "
                    f"{failure}")
            else:
                name_problem = sign_text_problem(entry.board)
                if name_problem:
                    entry.problems.append(
                        f"the wrapped board text for {display!r} is unusable: "
                        f"{name_problem}")
            destinations[tag] = entry
        else:
            existing.installation_ids.append(place["id"])
            existing.presets.append(preset)
            if place.get("duplicate_of") is None:
                existing.problems.append(
                    f"{place['id']} claims tag {tag!r} without declaring "
                    f"`duplicate_of`, so this would be a THIRD portal on that tag. "
                    f"Pairing draws uniformly at random among equal tags, so the "
                    f"pair becomes nondeterministic on every load")

    if hub is None:
        problems.append(
            f"{world} declares no placement with `portal.role: hub`, so there is "
            f"nowhere to put the directory board and no hub end for any spoke")
    else:
        declared = set(hub["tags"])
        found = set(destinations)
        for tag in sorted(declared - found):
            problems.append(
                f"the hub declares tag {tag!r} but no placement declares it as a "
                f"spoke, so that hub portal would have no partner")
        for tag in sorted(found - declared):
            problems.append(
                f"tag {tag!r} is declared by a spoke but not by the hub, so it would "
                f"have no hub end and would pair with nothing")

    order = sorted(destinations, key=lambda t: destinations[t].display)
    doc: dict[str, Any] = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "world": world,
        "owner": "WayFinding",
        "generated_by": "tools/jumpstart/network/waypoints.py registry",
        "note": (
            "Tag -> destination registry. This is the ONE source the browser map "
            "labels from and the ONE source the sign emitter names boards from, so a "
            "board and a map pin cannot disagree. A portal's tag is capped at 10 "
            "characters by TeleportWorld::Interact -> RequestText(.., 10); a sign's "
            "text is not capped at all on the `data=` path, so `display` is the "
            "readable name and `tag` is what the player sees on the arch."),
        "measured": {
            "portal_tag_visible_range_m": 5.0,
            "portal_tag_visible_range_source": (
                "Player::m_maxInteractDistance, initialised ldc.r4 5. in Player's "
                "constructor, gating Player::FindHoverObject; "
                "TeleportWorld::GetHoverText is the only place a tag is shown"),
            "sign_text_visible_range_m": 96.0,
            "sign_text_visible_range_source": (
                "ZNetScene::PointInsideActiveArea, Chebyshev 1.5 * m_zoneSize at the "
                "64 m zone size; Sign contains no distance test of its own. This is "
                "the range at which the sign EXISTS, not a claim that its glyphs are "
                "legible there"),
            "sign_prefab_probe": (
                "spawn_object sign + findObjects -prefab sign -detailed on live "
                "Ulfsland returned `Text: WayFinding probe -- delete me (author: )`; "
                "29 characters verbatim, author empty, deleted 1/1 afterwards"),
        },
        "hub": hub,
        "destinations": [destinations[t].as_dict() for t in order],
        "problems": problems + [p for t in order for p in destinations[t].problems],
    }
    return doc


def registry_path(world: str) -> Path:
    return DATA / world / "waypoints.yaml"


def write_registry(world: str) -> Path:
    doc = build_registry(world)
    path = registry_path(world)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# GENERATED by tools/jumpstart/network/waypoints.py -- do not edit.\n"
        "# Owner: WayFinding. Read-only to every other agent.\n"
        f"# Regenerate: python3 -m tools.jumpstart.network.waypoints registry {world}\n"
        + yaml.safe_dump(doc, sort_keys=False, width=100))
    return path


def map_labels(world: str) -> dict[str, str]:
    """tag -> display name, for the browser map.

    The map draws a portal's own ZDO tag on the glyph, which needs no manifest and
    works on any world. This is the second, nicer layer: where a tag is in the
    registry, the readable name is available too.
    """
    doc = yaml.safe_load(registry_path(world).read_text())
    return {d["tag"]: d["display"] for d in doc.get("destinations") or []}


# ---------------------------------------------------------------------------
# Ledger ops. BuildLedger owns the writer; this owns the dicts.
# ---------------------------------------------------------------------------

def ledger_ops(pieces: list[Piece], role: str,
               meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One `op:"spawn"` dict per piece, in BuildLedger's v0.1 envelope.

    `zdo_strings` carries the DECODED payload alongside `data_b64`, because a
    base64 blob is invisible in a replay diff: a wrong board and a right board are
    the same 52 opaque characters, and the whole reason this exists is that an
    unreadable label is the defect.
    """
    ops: list[dict[str, Any]] = []
    for piece in pieces:
        params: dict[str, Any] = {
            "role": role,
            "prefab": piece.prefab,
            "pos": [piece.x, piece.y, piece.z],
            "yaw_deg": piece.yaw,
            "rot": [piece.yaw, 0.0, 0.0],
            "from": [0.0, 0.0, 0.0],
        }
        expect: dict[str, Any] = {}
        if piece.text is not None:
            params["data_b64"] = sign_data(piece.text)
            params["zdo_strings"] = {"text": piece.text}
            expect = {"findObjects": {
                "prefab": piece.prefab,
                "near": [piece.x, piece.y, piece.z],
                "radius_m": 3.0,
                "text": piece.text,
            }}
        ops.append({
            "actor": "WayFinding",
            "op": "spawn",
            "params": params,
            "wire": [piece.spawn_command()],
            "requires": {"prefabs": [piece.prefab]},
            "expect": expect,
            "meta": dict(meta or {}),
        })
    return ops


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_registry(args: argparse.Namespace) -> int:
    doc = build_registry(args.world)
    if args.write:
        path = write_registry(args.world)
        print(f"wrote {path}")
    print(f"{args.world}: {len(doc['destinations'])} destination(s), "
          f"hub {(doc['hub'] or {}).get('installation')!r}")
    for dest in doc["destinations"]:
        spoke = dest.get("spoke") or {}
        print(f"  {dest['tag']:11} {dest['display']:28} "
              f"({spoke.get('x')}, {spoke.get('z')})  "
              f"{'+'.join(dest['installations'])}")
    if doc["problems"]:
        print("PROBLEMS:")
        for problem in doc["problems"]:
            print(f"  - {problem}")
        return 1
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    """The whole signage bill for a world, costed, with nothing placed.

    Three boards per destination and two for the hall, which is the entire cost of
    the legibility fix:
      * one guidepost beside the SPOKE portal, naming the hub, so somebody standing
        at a remote base knows the arch goes home;
      * one guidepost beside that destination's HUB portal, naming the destination,
        so somebody in the hall knows which arch is which;
      * one board at the hall door saying what the hall is;
      * one pointer at the world spawn temple saying the hall exists and where.
    """
    doc = build_registry(args.world)
    hub = doc["hub"] or {}
    destinations = doc["destinations"]
    hub_name = _display_name(str(hub.get("installation") or "portal hall"))
    print(f"{args.world}: signage bill\n")
    print(f"  hall board at ({hub.get('x')}, {hub.get('z')}):")
    for line in hall_sign_text(hub_name, len(destinations)).split("\n"):
        print(f"      | {line}")
    print()
    for dest in destinations:
        print(f"  {dest['tag']:11} hub-end board reads:")
        for line in dest["board"].split("\n"):
            print(f"      | {line}")
    print()
    if args.temple_x is not None and hub.get("x") is not None:
        bearing, compass, metres = bearing_and_range(
            (args.temple_x, args.temple_z), (hub["x"], hub["z"]))
        print(f"  spawn-temple pointer at ({args.temple_x}, {args.temple_z}) -> "
              f"bearing {bearing:.1f} deg ({compass}), {metres:.1f} m:")
        for line in pointer_text(hub_name, compass, metres).split("\n"):
            print(f"      | {line}")
        print()
    boards = 2 * len(destinations) + 2
    print(f"  TOTAL: {boards} boards + {boards} posts = {2 * boards} pieces")
    print(f"  Against the 9,290 pieces of the pre-bonemass tier, that is "
          f"{2 * boards / 9290 * 100:.2f}% of the build.")
    if doc["problems"]:
        print("\nPROBLEMS:")
        for problem in doc["problems"]:
            print(f"  - {problem}")
        return 1
    return 0


def _cmd_guidepost(args: argparse.Namespace) -> int:
    pieces = guidepost(args.x, args.z, args.ground_y, args.yaw, args.text)
    for piece in pieces:
        print(piece.spawn_command())
    if args.ledger:
        print(json.dumps(ledger_ops(pieces, "waypoint_sign"), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("registry", help="derive the tag -> destination registry")
    reg.add_argument("world")
    reg.add_argument("--write", action="store_true", help="write data/<World>/waypoints.yaml")
    reg.set_defaults(func=_cmd_registry)

    plan = sub.add_parser("plan", help="the costed signage bill; places nothing")
    plan.add_argument("world")
    plan.add_argument("--temple-x", type=float, default=None,
                      help="world spawn temple x, for the pointer board's bearing")
    plan.add_argument("--temple-z", type=float, default=None)
    plan.set_defaults(func=_cmd_plan)

    gp = sub.add_parser("guidepost", help="spawn lines for one free-standing signpost")
    gp.add_argument("--x", type=float, required=True)
    gp.add_argument("--z", type=float, required=True)
    gp.add_argument("--ground-y", type=float, required=True,
                    help="the walkable surface the post is driven into; never guessed")
    gp.add_argument("--yaw", type=float, required=True, help="direction the board faces")
    gp.add_argument("--text", required=True)
    gp.add_argument("--ledger", action="store_true", help="also print the ledger ops")
    gp.set_defaults(func=_cmd_guidepost)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
