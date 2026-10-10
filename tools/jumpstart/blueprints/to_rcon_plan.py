#!/usr/bin/env python3
"""Translate a PlanBuild-format blueprint into a World Edit Commands
`spawn_object` plan, to be sent over RCON through `consoleCommand`.

Why this exists
---------------
Infinity Hammer's blueprint commands (`hammer_blueprint`, `hammer_restore`,
`hammer_save`) are **admin-client only**: MEASURED, `hammer_restore` calls
`InfinityHammer.Hammer::Equip()`, which calls
`ServerDevcommands.Helper::GetPlayer()` and then `Humanoid::EquipItem`, so it
needs a live local player holding a hammer.  There is no headless path through
Infinity Hammer.

World Edit Commands 1.77.0 -- already deployed server-side -- **is** headless,
and unlike vanilla `spawn` it takes the blueprint's own per-piece ZDO state:
`spawn_object <prefab> pos= rot= data=<base64>`.  MEASURED on a throwaway
dedicated server with zero players (see
`docs/proposed/2026-09-15-blueprint-tooling-evaluation.md` §4): the 14th column
lands on the new ZDO, including a container's full inventory.  A treasure chest
came out of a blueprint already holding its coins with nobody online.

What is carried, and what the game recomputes
---------------------------------------------
  * rotation  -- preserved.  `rot=` is euler degrees; see `rot_arg` for the
                 component ORDER, which is not the vanilla one.
  * data      -- CARRIED.  Column 13 is passed through byte-for-byte; that is
                 per-piece health, support, scale, creator, the Infinity
                 Hammer / Structure Tweaks component overrides, sign text,
                 item-stand contents and **container inventories**.
  * scale     -- carried for the 5,326 rows that state a non-unit scale in
                 columns 10-12 and have no data column, by writing the same
                 `scale` vector3 key the other 67,803 corpus rows use.  For a
                 row that HAS a data column the column wins untouched: 670
                 corpus rows state a non-unit scale that their own payload does
                 not repeat, and `--merge-scale` is how a placement opts into
                 fixing that at the cost of re-encoding.
  * item stands -- 4 corpus rows write the item NAME in column 13 instead of a
                 payload (`...;1;1;1;ShieldWood`).  Those are translated into
                 the `item` string key that the other 873 item-stand rows use,
                 rather than dropped.
  * support   -- still recomputed by the game.  MEASURED: a `support` float sent
                 in `data=` does NOT survive, because WearNTear recomputes it on
                 Awake.  Emit in bottom-up Y order (the default here) so each
                 piece has its support below it already placed.
  * creator   -- carried, from the column's `creator` long.  `--strip-identity`
                 drops it along with the 8,620 rows' worth of third-party Steam
                 IDs and names.

Loot and creatures
------------------
Blueprints capture whatever was selected, and several in this corpus carry
hundreds of `TreasureChest_*` objects.  Those are filtered out by default;
`--keep-loot` and `--keep-creatures` opt back in deliberately.

Usage
-----
    python3 to_rcon_plan.py BASE.blueprint --at 120 32 -450 [--rotate 90]
        [--out plan.txt] [--verify] [--keep-loot] [--keep-creatures]
        [--no-drop-co-located-copies] [--no-reorient-upside-down-panels]
        [--no-upright-panels] [--no-seat-unsupported-bodies]
        [--no-drop-subgrade-rows] [--no-reseat-blocked-beds]

The preflight's deterministic repairs run BY DEFAULT, because the orchestrated
build calls this with no repair flags at all; every row a repair drops, turns
over, yaws, lowers or restores is named on stderr and in
`plan.txt.repairs.json`.

The plan is one World Edit Commands command per line. Send it with
`../terraform/place.py`, which is the only thing that runs it correctly:

  * a plan line is a CONSOLE command, so it has to go through ValheimRcon's
    `consoleCommand` bridge. MEASURED against the deployed ValheimRcon 1.6.2,
    `spawn_object` sent as a bare RCON command answers `Unknown command
    spawn_object` -- ValheimRcon's own spawn verb is called `spawn` and takes
    positional coordinates.
  * Server Devcommands has `Multiple commands per line = true` in the deployed
    config, so `place.py` batches ~43 lines per round trip and this body lands
    in 45 round trips and 1.8 s rather than 1,907 round trips and a minute.
    That is throughput only -- round trips cost 2-4 Unity frames each.
  * PLACEMENT IS SAFE TO BATCH; VERIFICATION IS THE DANGEROUS HALF. This
    docstring used to blame the wedge on sustained placement traffic, and that
    was WRONG. MEASURED from ValheimRcon 1.6.2's IL: `Log.Message` writes the
    full `CommandResult.Text` on the Unity main thread before truncation, and
    `InvokeConsoleCommand.OnHandle` returns only `Command '<cmd>' executed.`,
    never the console output -- so a placement batch's logged bytes are capped
    by the 4050-byte payload limit no matter how large the world is. The
    unbounded write belongs to `findObjects`, whose per-ZDO listing IS the
    logged text. See `../terraform/rcon.py` for the instruction offsets.
  * so do NOT verify a placement by listing it. `verify_placement.py` counts
    with `objects_count` and lists only the four extreme pieces, under a hard
    output budget; `rcon.py::guard` now refuses an unscoped `findObjects`
    outright.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from inventory import (  # noqa: E402  (local module, path set above)
    FORAGE_RE,
    LOOT_RE,
    SPAWN_RE,
    LegacyTerrainFormat,
    _f,
    header_section,
    load_evidence,
    resolve,
)
import base_geometry  # noqa: E402  (local module, path set above)
import data_entry  # noqa: E402  (local module, path set above)
import materialise  # noqa: E402  (checksum-bound authored additions)
import preflight  # noqa: E402  (local module, path set above)
from fixtures import PORTAL_PREFABS  # noqa: E402  (the ONE set of prefabs that teleport)

RAD = 180.0 / math.pi

# CARRIED_PORTALS_DEPENDS_ON: "drop" is the rule; "emit" restores the shipped
# emitter, in which a portal inside a body reached the plan untagged unless
# the CALLER remembered `--drop-prefab portal_wood`.  Set only by the neuter
# in `settlements/build.py::_case_NEUTER_emit_carried_portals_*`.
CARRIED_PORTALS = "drop"


# --------------------------------------------------------------------------
# Unity rotation maths
# --------------------------------------------------------------------------
def quat_to_euler(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    """Unity `Quaternion.eulerAngles`, in degrees.

    Unity composes `Quaternion.Euler(ex, ey, ez)` as ``Ry(ey) * Rx(ex) * Rz(ez)``
    -- the YXZ convention -- so the decomposition must invert that same order.
    Writing ``M = Ry*Rx*Rz`` out gives

        M[1][2] = -sin(ex)
        M[1][0] / M[1][1] = tan(ez)
        M[0][2] / M[2][2] = tan(ey)

    which is what the general branch below evaluates.  At ``ex = +-90`` the
    matrix collapses to ``[cos(ey-+ez), sin(ey-+ez)]`` in its top row: only the
    sum or difference of yaw and roll survives, so roll is pinned to zero and
    yaw absorbs it.  Getting that fold wrong is a silent 50-degree error on
    roughly one piece in twenty thousand, which is why it is handled here
    rather than approximated.

    Angles come back in ``[0, 360)`` exactly as Unity reports them, which is
    also the form `spawn -rotation` expects.
    """
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n == 0.0:
        return (0.0, 0.0, 0.0)
    x, y, z, w = x / n, y / n, z / n, w / n

    sinp = 2.0 * (w * x - y * z)  # -M[1][2]
    sinp = 1.0 if sinp > 1.0 else (-1.0 if sinp < -1.0 else sinp)
    pitch = math.asin(sinp)
    if abs(sinp) > 1.0 - 1e-9:  # gimbal lock
        m00 = 1.0 - 2.0 * (y * y + z * z)
        m01 = 2.0 * (x * y - w * z)
        yaw = math.atan2(m01, m00) if sinp > 0 else math.atan2(-m01, m00)
        roll = 0.0
    else:
        yaw = math.atan2(2.0 * (w * y + x * z), 1.0 - 2.0 * (x * x + y * y))
        roll = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (x * x + z * z))
    out = [pitch * RAD, yaw * RAD, roll * RAD]
    return tuple((a + 360.0) % 360.0 for a in out)  # type: ignore[return-value]


def euler_to_quat(ex: float, ey: float, ez: float) -> tuple[float, float, float, float]:
    """Unity `Quaternion.Euler(ex, ey, ez)`, degrees in, xyzw out."""
    hx, hy, hz = (a / RAD * 0.5 for a in (ex, ey, ez))
    sx, cx = math.sin(hx), math.cos(hx)
    sy, cy = math.sin(hy), math.cos(hy)
    sz, cz = math.sin(hz), math.cos(hz)
    # q = Ry * Rx * Rz
    qy = (0.0, sy, 0.0, cy)
    qx = (sx, 0.0, 0.0, cx)
    qz = (0.0, 0.0, sz, cz)

    def mul(a, b):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )

    return mul(mul(qy, qx), qz)


def quat_angle_between(a, b) -> float:
    """Angle in degrees between two rotations, sign-insensitive."""
    dot = abs(sum(p * q for p, q in zip(a, b)))
    dot = min(1.0, max(-1.0, dot))
    return 2.0 * math.acos(dot) * RAD


def yaw_quat(deg: float) -> tuple[float, float, float, float]:
    h = deg / RAD * 0.5
    return (0.0, math.sin(h), 0.0, math.cos(h))


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qrot(q, v):
    """Rotate vector v by quaternion q."""
    x, y, z, w = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


# --------------------------------------------------------------------------
class Obj:
    __slots__ = ("prefab", "pos", "rot", "scale", "info", "data")

    def __init__(self, prefab, pos, rot, scale, info, data):
        self.prefab = prefab
        self.pos = pos
        self.rot = rot
        self.scale = scale
        self.info = info
        self.data = data


def read_objects(path: Path) -> list[Obj]:
    """Read the source with checksum-bound authored additions and poses."""
    repair = materialise.authored_repair(path)
    out = _read_objects(path)
    if repair is not None:
        apply_authored_poses(out, repair.get("poses", []))
        out.extend(_read_objects(materialise.AUTHORED_REPAIRS.parent /
                                 repair["addition"]))
    return out


def apply_authored_poses(objs: list[Obj], poses: list[dict]) -> None:
    """Change only an explicitly identified source row's quaternion."""
    for pose in poses:
        row = pose["row"]
        if not 0 <= row < len(objs):
            raise ValueError(f"authored pose row {row} is absent from source")
        obj = objs[row]
        if (obj.prefab != pose["prefab"] or tuple(obj.pos) != tuple(pose["pos"])
                or tuple(obj.rot) != tuple(pose["from_rot"])):
            raise ValueError(f"authored pose row {row} does not match source identity")
        obj.rot = tuple(pose["to_rot"])


def _read_objects(path: Path) -> list[Obj]:
    """Read raw .blueprint/.vbuild objects without composing authored repairs.

    Field layouts are documented in `inventory.py` and were measured from
    InfinityHammer.dll 1.83.0.
    """
    out: list[Obj] = []
    text = path.read_text("utf-8", "replace")
    if path.suffix.lower() == ".vbuild":
        for row in text.splitlines():
            line = row.rstrip("\r")
            if not line.strip():
                continue
            if "," in line:
                line = line.replace(",", ".")
            p = line.split(" ")
            name = p[0].strip()
            if not name:
                continue
            out.append(
                Obj(
                    name,
                    (_f(p, 5, 0.0), _f(p, 6, 0.0), _f(p, 7, 0.0)),
                    (_f(p, 1, 0.0), _f(p, 2, 0.0), _f(p, 3, 0.0), _f(p, 4, 0.0)),
                    (1.0, 1.0, 1.0),
                    p[8] if len(p) > 8 else "",
                    "",
                )
            )
        return out

    # Sections come from inventory.header_section, which mirrors Infinity
    # Hammer's own state machine: an unrecognised header discards every row
    # after it. That mirroring is deliberate for `inventory.py`'s reporting and
    # is the reason `spawn circle` never reached a server, but it is a BUG to
    # inherit here, and the shape of the bug is not what it looks like.
    #
    # MEASURED across the catalogued corpus: the `#Terrain` sections hold 463
    # fourteen-field PIECE rows and only 16 genuine terrain operations. The
    # props are 391 x `FirTree_oldLog`, 70 x `stubbe` and 2 x `lox_ribs`,
    # written under a `#Terrain` header by whichever tool produced the RustyMods
    # VikingNPC packs; 308 of them even carry a data column.
    # `BlackForestRaiderTown1` loses 187 of its 1,090 rows to this -- 17.2% of
    # the file, all props. So `#Terrain` is read here, and the discrimination is
    # by FIELD COUNT, not by prefab name: a PlanBuild terrain operation is
    # `shape;x;y;z;radius;rotation;smooth;` (8 fields at most) and a piece row
    # needs at least 13 to reach its scale. Name-based filtering cannot work --
    # `circle` and `square` both resolve `vanilla` in the prefab evidence.
    #
    # Every OTHER unrecognised header still discards its rows. The vendored
    # `terrain-future-section.blueprint` contract fixture states exactly that:
    # "Unknown sections must not inherit the preceding parser state".
    section: str | None = "#pieces"
    for row in text.splitlines():
        if not row:
            continue
        if row.startswith("#"):
            try:
                section, _key = header_section(row, section)
            except LegacyTerrainFormat as exc:
                raise LegacyTerrainFormat(
                    f"{path}: {exc}. Infinity Hammer refuses this file outright; "
                    f"re-save it with a current version."
                ) from None
            if section is None and row.split(":", 1)[0].strip().lower() == "#terrain":
                section = "#terrain"
            continue
        if section not in ("#pieces", "#terrain"):
            continue
        line = row.replace(",", ".") if "," in row else row
        p = line.split(";")
        name = p[0].strip()
        if not name:
            continue
        if section == "#terrain" and len(p) < 13:
            continue  # a genuine terrain operation; this emitter cannot place one
        out.append(
            Obj(
                name,
                (_f(p, 2, 0.0), _f(p, 3, 0.0), _f(p, 4, 0.0)),
                (_f(p, 5, 0.0), _f(p, 6, 0.0), _f(p, 7, 0.0), _f(p, 8, 0.0)),
                (_f(p, 10, 1.0), _f(p, 11, 1.0), _f(p, 12, 1.0)),
                p[9] if len(p) > 9 else "",
                p[13] if len(p) > 13 else "",
            )
        )
    return out


def fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# --------------------------------------------------------------------------
# `spawn_object` argument order -- the highest-risk part of this converter
#
# `spawn_object` uses a DIFFERENT component order for `pos=` and for `rot=`,
# and a THIRD one for `from=`. All three were read out of the IL of the
# deployed assemblies, and the position order was then confirmed against a real
# saved ZDO, because the help text and the parameter name both mislead.
# --------------------------------------------------------------------------
def pos_arg(x: float, y: float, z: float) -> str:
    """`spawn_object pos=` takes **z,x,y**.

    MEASURED TWICE, because the first measurement was wrong and would have
    shipped every building rotated 90 degrees about the vertical axis about the
    site centre:

    1. From IL. `WorldEditCommands.SpawnObjectParameters` parses `pos=` with
       `ServerDevcommands.Parse::VectorZXYRange`, whose body is
       `x = Float(args, index+1); y = Float(args, index+2);
        z = Float(args, index+0)`. So token 0 is Z, token 1 is X, token 2 is Y.
       It is NOT `Parse::VectorXZY` -- that is what the same command uses for
       `from=`/`refpos=`, and its `" (vec x,z,y)"` help text is what makes
       `pos=` look like x,z,y when it is not.
    2. From a saved world. `pos=1971.7835,1998.76,40.395` sent headlessly to a
       sandbox dedicated server produced a ZDO at `m_position` =
       (1998.76, 40.395, 1971.784) -- token 0 in Z, token 1 in X, token 2 in Y,
       exactly as the IL says.

    Vanilla `spawn <x> <y> <z>` is x,y,z. Getting this wrong raises no error:
    the building simply lands with X and Z exchanged, which for a square
    footprint looks almost right. `test_jumpstart_data_column.py` pins it with
    a position whose three components are all different, because a fixture like
    `(5, 5, 5)` passes under any permutation and proves nothing.
    """
    return f"{fmt(z)},{fmt(x)},{fmt(y)}"


def rot_arg(ex: float, ey: float, ez: float) -> str:
    """`spawn_object rot=` takes euler **y,x,z**.

    MEASURED from IL: parsed by `ServerDevcommands.Parse::VectorYXZRange`, whose
    body is `x = Float(args, index+1); y = Float(args, index+0);
    z = Float(args, index+2)` -- token 0 is Y (yaw), token 1 is X, token 2 is Z.
    Vanilla `spawn ... -rotation` is `Quaternion::Euler(x, y, z)` in x,y,z
    order. Yaw is the component that matters most for a building, and yaw is
    FIRST here.
    """
    return f"{fmt(ey)},{fmt(ex)},{fmt(ez)}"


# `pos=` is a RELATIVE position: `SpawnObjectParameters` stores it in
# `RelativePosition` and adds it to `From`. MEASURED: `From` defaults to a
# PLAYER's position when one can be resolved ("Unable to find the player." /
# "Player doesn't have a public position." are its failure strings), and only
# falls back to the origin when there is nobody online. A plan that relies on
# that fallback silently moves the moment somebody logs in, so every command
# pins the reference explicitly. `from=` is parsed by `Parse::VectorXZY` -- a
# THIRD order -- and the origin is the one value that is identical under all
# six permutations, which is why it is written as three zeroes rather than
# omitted.
FROM_ORIGIN = "from=0,0,0"


PASS_THROUGH = "pass-through"
SYNTH_SCALE = "scale-only"
SYNTH_ITEM = "item-name"
MERGED = "merged"
NONE = "none"


def data_for(
    obj: Obj, *, strip_identity: bool, merge_scale: bool
) -> tuple[str, str, data_entry.DataEntry | None]:
    """The `data=` value for one row, how it was arrived at, and what it holds.

    The default for a row that carries a decodable column 13 is to emit that
    STRING UNCHANGED. `data_entry.encode` is proven byte-exact on all 125,576
    decodable corpus payloads, so re-encoding would in fact reproduce the same
    bytes -- but the game reads these bytes, the proof is a property of today's
    corpus rather than of every future body, and there is no upside. Verbatim is
    the only form that cannot drift.

    The decoded entry is returned so the caller can report on the payload
    without decoding it a second time.
    """
    raw = obj.data.strip()
    nonunit = any(abs(s - 1.0) > 1e-4 for s in obj.scale)
    if raw:
        try:
            entry = data_entry.decode(raw)
        except data_entry.DataEntryError:
            # Not a payload. MEASURED: 4 corpus rows put the item NAME here
            # (`itemstand;;...;1;1;1;ShieldWood`). The 873 well-formed
            # item-stand rows carry that name as the `item` string key, so the
            # plain form translates into exactly that and nothing more -- stack,
            # quality and durability are not stated by the plain form and are
            # left for the game to default.
            synth = data_entry.DataEntry()
            synth.strings[data_entry.stable_hash("item")] = raw
            if nonunit:
                synth.vecs[data_entry.stable_hash("scale")] = obj.scale
            return synth.encode(), SYNTH_ITEM, synth
        changed = False
        if strip_identity:
            entry = entry.without_identity()
            changed = True
        if merge_scale and nonunit and data_entry.stable_hash("scale") not in entry.vecs:
            entry.vecs[data_entry.stable_hash("scale")] = obj.scale
            changed = True
        if changed:
            return entry.encode(), MERGED, entry
        return raw, PASS_THROUGH, entry
    if nonunit:
        # Nothing to preserve, so nothing can be corrupted: state the scale the
        # row itself states, under the `scale` vector3 key that 67,803 corpus
        # rows already use for it.
        synth = data_entry.DataEntry()
        synth.vecs[data_entry.stable_hash("scale")] = obj.scale
        return synth.encode(), SYNTH_SCALE, synth
    return "", NONE, None



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blueprint")
    ap.add_argument(
        "--at",
        nargs=3,
        type=float,
        required=True,
        metavar=("X", "Y", "Z"),
        help="world position the blueprint origin lands on",
    )
    ap.add_argument("--rotate", type=float, default=0.0, help="extra yaw in degrees")
    ap.add_argument(
        "--align",
        default="floor-center",
        choices=("raw", "ground", "center", "ground-center", "floor", "floor-center"),
        help=(
            "how the blueprint's own origin maps onto --at. 'floor-center' (default) "
            "puts the centre of the body's MEASURED XZ footprint AT THIS YAW on --at XZ "
            "-- base_geometry.xz_footprint, collider corners rather than pivots, taken "
            "after --rotate is applied -- and the blueprint's FLOOR PLANE on --at Y, "
            "where the floor plane is measured by base_geometry.floor_datum from the "
            "same colliders; 'floor' does the Y half only and so anchors XZ on the "
            "blueprint's own origin, which for a corner-origin body like "
            "BjOrN_blueprint001 puts the CORNER on --at and the building 33 m off the "
            "pad in each axis. 'ground' and "
            "'ground-center' are the OLD Y rule, min pivot Y, kept only for comparison: "
            "a pivot is not a surface, and 99 of the 157 non-empty .blueprint bodies in "
            "this corpus are already normalised to min pivot Y == 0, on which that shift "
            "is always exactly zero. 'center' does XZ only; 'raw' trusts the file."
        ),
    )
    ap.add_argument(
        "--base-y",
        type=float,
        help=(
            "override the measured floor plane for the floor/floor-center modes, in "
            "blueprint-local metres. For a multi-storey body captured on a slope there "
            "is no single correct plane; this is how a placement DECLARES which one it "
            "means. Recorded in placements.yaml as blueprint_datum.override_base_y with "
            "a written basis."
        ),
    )
    ap.add_argument(
        "--allow-unplaceable",
        action="store_true",
        help="emit a plan even when the floor plane cannot be established (NOT "
             "recommended: the blueprint lands on its raw origin)",
    )
    # THE THREE PREFLIGHT OVERRIDES.  Each one is a DECLARATION that the
    # caller has looked at a measured refusal and accepted it, recorded in
    # the run's stderr with its numbers either way.  None of them is a
    # `--force`: they are separate because the three defects have three
    # different remedies and lumping them would let an unnoticed one ride
    # in on a deliberate one.
    ap.add_argument(
        "--allow-unsupported-bodies",
        action="store_true",
        help="emit even when a body in this blueprint stands clear of a "
             "DEAD FLAT pad at its own datum by more than tan(38 deg) per "
             "metre. Five sites on Ulfsland were built from three files "
             "that do this, and the buildings cannot be walked into.",
    )
    ap.add_argument(
        "--allow-inverted-panels",
        action="store_true",
        help="emit even when an oriented piece rises AWAY from its own "
             "body's ridge. A 180 degree yaw moves neither the footprint "
             "nor the AABB, so nothing except the signed slope sees this.",
    )
    ap.add_argument(
        "--allow-co-located-rows",
        action="store_true",
        help="emit even when two rows of one prefab share a place within "
             "5 cm on all three axes; each pair costs a second ZDO that "
             "nobody can see. Refused only when --drop-co-located-copies "
             "is off, or when a co-located row is NOT inside its kept "
             "siblings (or has no measured solid) and so cannot be dropped "
             "without opening space.",
    )
    ap.add_argument(
        "--allow-unsheltered-deck",
        action="store_true",
        help="emit even when a deck column of this blueprint is ENCLOSED BY "
             "WALLS and OPEN TO THE SKY. There is no deterministic repair "
             "the emitter applies: preflight.shelter_plan is REPORTED only, "
             "because MEASURED 22 of the 23 holes it was ever pointed at are "
             "flues over a fire (bonfire, smelter, charcoal_kiln, forge, "
             "brazier), and a panel over a flue puts the fire out. So this "
             "refusal has an override and no repair, and the override is "
             "the operator saying the hole is acceptable. "
             "MEASURED on Ulfsland: 12 refusals over six bodies from TWO "
             "files, hs_ashlands_neletit_portalhubtower (8 at portal-hall "
             "and 8 at hognest-castle) and outposttower (8/8/5 at wt-peak, "
             "wt-east, wt-spawn).",
    )
    ap.add_argument(
        "--allow-roof-skin-gaps",
        action="store_true",
        help="emit even when the roof skin of this body, AT THE --rotate IT "
             "IS EMITTED AT, jumps by more than the gate's own allowance "
             "between adjacent 0.5 m columns with nothing in the gap "
             "(structures/roof_skin). The gate's lattice has the WORLD's "
             "axes, so the verdict belongs to the file at a yaw, not to the "
             "file: MEASURED, hs_blackforest_kin_01_woodhouse has such a "
             "hole at 11 of 360 whole-degree yaws and "
             "hs_blackforest_tester_meadows_log_cabin at 63, and both are "
             "clean at yaw 0. No repair is applied by the emitter: the "
             "answer is a checksum-bound addition in "
             "blueprints/data/authored_repairs.json, which read_objects "
             "composes, and this override writes the hole into the world.",
    )
    ap.add_argument(
        "--allow-subgrade-rows",
        action="store_true",
        help="emit even when rows COMPLETELY under the pad plane - own top "
             "and all - are still in the plan, each a spawn_object, a ZDO "
             "and an acceptance refusal that nobody can see. They remain "
             "only when --no-drop-subgrade-rows switched the drop off or "
             "when the drop was REFUSED because such a row carries a kept "
             "row (subgrade_structural); either way this emits every row as "
             "authored. MEASURED on Ulfsland: 414 such pieces in 45 bodies, "
             "seven of the nine signatures being authored sub-floor courses "
             "that --align floor-center seated a flat pad over.",
    )
    ap.add_argument(
        "--allow-blocked-seats",
        action="store_true",
        help="emit even when a claimable bed of this blueprint is a spawn "
             "seat the acceptance gate's own spawn.probe_seat refuses - too "
             "little room or headroom for the player's measured capsule, or "
             "inside a solid - and no move inside its own room clears it. "
             "MEASURED on Fresh42 drive 3: 14 spawn refusals over 7 beds, "
             "all row 32 of hs_blackforest_kin_01_woodhouse.blueprint under "
             "its own 0.449 m wood_beam rail.",
    )
    # THE REPAIRS, ON BY DEFAULT.  Deterministic, derived from the same
    # measurement that raised the refusal, and re-measured afterwards - the
    # acceptance is the check coming out clean.  They were opt-in, the
    # selection gate passed them and the orchestrated build did not: MEASURED
    # over Fresh42's 43 placed units, 43 emit with them and 31 REFUSE without
    # (subgrade_rows 9, inverted_panels 26, unsupported_body 5).  A repair a
    # human has to remember is not a repair on a fresh seed, so each one is
    # the default and `--no-<flag>` reproduces the unrepaired emitter.
    ap.add_argument(
        "--upright-panels",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="yaw each inverted oriented piece 180 degrees about its own "
             "pivot so it rises toward its own body's ridge. The pivot "
             "does not move; this is the exact inverse of the flip that "
             "inverted it. Repeated for rows the first yaws expose, never "
             "twice on one row; a row the yaw does not cure is restored as "
             "authored and named. Never applied to an upside-down row. On "
             "by default.",
    )
    ap.add_argument(
        "--reorient-upside-down-panels",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="turn each upside-down oriented piece the right way up IN ITS "
             "OWN SOLID: the pose (its own prefab under any axis-aligned "
             "rotation of its frame, or its mirror twin <name> <-> "
             "<name>_upsidedown) that reproduces the authored collider "
             "vertices within 1 cm and that the orientation instrument "
             "calls upright and rising toward the body's ridge. A row with "
             "no such pose is left as authored and named. On by default.",
    )
    ap.add_argument(
        "--drop-co-located-copies",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="remove each row that the duplicate census would call a copy "
             "- same prefab, pivot within 5 cm on all three axes of a "
             "sibling, and its own solid at least 98 percent inside the "
             "KEPT siblings (structures.buried_fraction) - keeping the "
             "first-authored row of each group and naming every row "
             "dropped. A shared pivot whose rows each fill their own space "
             "(two half-arches, a valley) is kept. On by default.",
    )
    ap.add_argument(
        "--seat-unsupported-bodies",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="lower each unsupported body by its own MEASURED support gap, "
             "so its first point of contact lands on what is under it and "
             "no point goes below. The body's internal geometry is "
             "untouched: a body moves, never a piece. On by default.",
    )
    ap.add_argument(
        "--seat-sunk-props",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="lift each ground-resting piece whose base is more than the "
             "datum tolerance under the floor plane (`props_below_floor`) "
             "onto that plane - where it would otherwise be sunk in the pad, "
             "and a sunk fire is switched off by the ground - unless the "
             "lift drives it into another physical solid, in which case it "
             "is left and named with what it would enter. On by default.",
    )
    ap.add_argument(
        "--drop-subgrade-rows",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="remove every row whose OWN WHOLE-SOLID TOP is under the pad "
             "plane (the gate's `buried`, asked one row at a time), naming "
             "each one, its position and its depth on stderr and in "
             "OUT.repairs.json - and REFUSE the drop instead if any such row "
             "carries a kept row that does not reach the pad itself. An "
             "invisible piece is not a piece: the alternative, seating the "
             "pad at the course's own bottom, MEASURED over the 227 library "
             "bodies that carry such a course, lifts the walkable floor out "
             "of step reach on 210 of them and leaves 11,179 course rows in "
             "the air on 117. On by default.",
    )
    ap.add_argument(
        "--reseat-blocked-beds",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="move each claimable bed whose seat the gate's spawn.probe_seat "
             "refuses the SMALLEST distance inside its own room, on its own "
             "floor, that clears the player's measured capsule with the "
             "bed's own body entering nothing deeper than it did as "
             "authored (preflight.reseat_plan); the new pose is re-probed by "
             "the gate's function before it is taken, and every moved row is "
             "named with its before/after clearance and headroom on stderr "
             "and in OUT.repairs.json. On by default.",
    )
    ap.add_argument("--out", help="write the plan here instead of stdout")
    ap.add_argument("--keep-loot", action="store_true")
    ap.add_argument("--keep-creatures", action="store_true")
    ap.add_argument("--keep-forage", action="store_true")
    ap.add_argument(
        "--drop-prefab",
        action="append",
        default=[],
        metavar="PREFAB",
        help=(
            "omit this prefab entirely; repeatable. Community bases tend to carry the "
            "whole station set, so a Plains-tier base often ships a black forge or an "
            "eitr refinery. Dropping those keeps a tiered jumpstart tiered."
        ),
    )
    ap.add_argument(
        "--evidence",
        default=str(HERE / "data" / "prefab_evidence.json"),
        help="resolved prefab evidence used to reject unspawnable names",
    )
    ap.add_argument(
        "--allow-missing-prefabs",
        action="store_true",
        help="emit spawns even for prefabs the evidence cannot resolve",
    )
    ap.add_argument(
        "--no-data",
        action="store_true",
        help=(
            "emit `spawn_object` without `data=`, discarding every per-piece "
            "payload. This is what this converter did before the data column was "
            "carried; keep it only to reproduce an old plan."
        ),
    )
    ap.add_argument(
        "--strip-identity",
        action="store_true",
        help=(
            "remove steamID, steamName, creator, crafterID, crafterName and "
            "xray_created from each payload. 8,620 corpus rows carry a third "
            "party's Steam ID and display name. This RE-ENCODES the payload, "
            "which is safe only because the encoder is proven byte-exact on all "
            "125,576 decodable corpus payloads -- see the round-trip test."
        ),
    )
    ap.add_argument(
        "--merge-scale",
        action="store_true",
        help=(
            "for the 670 corpus rows whose columns 10-12 state a non-unit scale "
            "their own payload does not repeat, add the `scale` key. Also "
            "re-encodes; same proof applies."
        ),
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="check the quaternion -> euler -> quaternion round trip on every object",
    )
    args = ap.parse_args(argv)

    path = Path(args.blueprint)
    objs = read_objects(path)
    if not objs:
        print(f"{path}: no objects", file=sys.stderr)
        return 1

    # A PORTAL CARRIED INSIDE A BODY NEVER REACHES A PLAN, whatever the
    # caller asked for.  It arrives with its AUTHOR's tag or none, and
    # MEASURED from `Game::FindRandomUnconnectedPortal` pairing is exact tag
    # equality with a uniform random draw among the equals -- so a carried
    # portal is a door to a random blank-tag mod-location portal.  Every
    # portal on a world is placed deliberately, TAGGED, by the tool that owns
    # its pair (`terraform/stock.py`, `settlements/build.py::apply_portals`).
    # MEASURED on Fresh42 drive 2: the per-placement `drop_prefabs` rule was
    # the only guard, `settlements/build.py` passes none, and the castle
    # bodies `hs_ashlands_neletit_portalhubtower` (15) and
    # `hs_mistlands_sheepshank_trophytowerv2` (1) stood 16 BLANK-tag portals.
    # No override: nothing on this tree wants a carried portal spawned.
    drop = set(args.drop_prefab)
    dropped = {"loot": 0, "creature": 0, "forage": 0, "explicit": 0,
               "untaggable_portal": 0}
    kept: list[Obj] = []
    for o in objs:
        if CARRIED_PORTALS == "drop" and o.prefab in PORTAL_PREFABS:
            dropped["untaggable_portal"] += 1
            continue
        if o.prefab in drop:
            dropped["explicit"] += 1
            continue
        if not args.keep_loot and LOOT_RE.match(o.prefab):
            dropped["loot"] += 1
            continue
        if not args.keep_creatures and SPAWN_RE.match(o.prefab):
            dropped["creature"] += 1
            continue
        if not args.keep_forage and FORAGE_RE.match(o.prefab):
            dropped["forage"] += 1
            continue
        kept.append(o)

    # Every name that survives the drop filters becomes a literal `spawn <name>`
    # against a live server, so check the names against the bundle-derived
    # evidence before emitting anything. A MISSING prefab is a command the game
    # will reject; an unknown one is a name nobody has ever confirmed exists.
    #
    # "NOT IN THE EVIDENCE FILE" IS A STATEMENT ABOUT THE EVIDENCE, NEVER
    # ABOUT THE PREFAB, and collapsing the two is how this gate refused two
    # bodies for its own corpus's coverage.  MEASURED, from the files alone:
    # `data/prefab_evidence.json` says in its OWN `note` that it is "pruned
    # from piece_prefabs.json: only prefabs referenced by the inventoried
    # blueprints", and that population is the 112 files in
    # `data/inventory.json` -- NOT ONE of which is any of the 18 bodies
    # `settlements/plan.py` selects.  The prune covered 16 of the 18 by
    # vocabulary overlap and said nothing about the two it had never seen:
    #   hs_ashlands_swampytreehouse          22 names (Eyescream, QueensJam,
    #                                        MeadBzerker, MeatPlatter, ...)
    #   hs_blackforest_crimsonchaostownhall  Placeable_HardRock
    # ALL 23 RESOLVE `vanilla` AGAINST `piece_prefabs.json`, THE FULL EVIDENCE
    # THE PRUNE WAS CUT FROM.  The game can spawn every one of them, and
    # `--drop-prefab` would have retired 23 pieces that should stand.
    #
    # So a name the prune does not carry is re-asked of the FULL evidence --
    # same file, same `inventory.resolve`, no second definition -- and only a
    # name MISSING *there* is a refusal.  A prune cannot widen a refusal about
    # a question it was never asked.
    # THE SAME EVIDENCE, AS A PREDICATE, for a prefab a REPAIR introduces:
    # `--reorient-upside-down-panels` may turn a row into its mirror twin
    # (`<name>` <-> `<name>_upsidedown`), which this blueprint may never have
    # carried, so the check above has never been asked about it.  Same file,
    # same `inventory.resolve`, same prune-then-full rule; None under
    # `--allow-missing-prefabs`, where the caller has waived the question.
    spawnable_name = None
    if not args.allow_missing_prefabs:
        ev = Path(args.evidence)
        if not ev.exists():
            print(f"{ev}: no prefab evidence; pass --allow-missing-prefabs to skip the check",
                  file=sys.stderr)
            return 2
        resolution = json.loads(ev.read_text("utf-8"))["resolution"]
        full_evidence: list = []

        def spawnable_name(name: str) -> bool:
            verdict = resolution.get(name)
            if verdict is not None:
                return verdict != "MISSING"
            full = ev.parent / "piece_prefabs.json"
            if not full.exists():
                return False
            if not full_evidence:
                full_evidence.append(load_evidence(full))
            return resolve(name, *full_evidence[0]) != "MISSING"

        bad: dict[str, str] = {}
        uncovered: set[str] = set()
        for o in kept:
            verdict = resolution.get(o.prefab)
            if verdict is None:
                uncovered.add(o.prefab)
            elif verdict == "MISSING":
                bad[o.prefab] = f"MISSING from every bundle and deployed mod ({ev.name})"
        if uncovered:
            full = ev.parent / "piece_prefabs.json"
            if not full.exists():
                for name in uncovered:
                    bad[name] = (f"outside {ev.name}'s pruned population, and "
                                 f"{full.name} is not present to resolve it against")
            else:
                vanilla, mods = load_evidence(full)
                covered = {}
                for name in sorted(uncovered):
                    verdict = resolve(name, vanilla, mods)
                    if verdict == "MISSING":
                        bad[name] = (f"MISSING from every bundle and deployed "
                                     f"mod ({full.name})")
                    else:
                        covered[name] = verdict
                if covered:
                    shown = sorted(covered.items())
                    print(f"  evidence: {len(covered)} prefab(s) are outside "
                          f"{ev.name}'s pruned population and were resolved "
                          f"against the full {full.name}: "
                          + ", ".join(f"{k}={v}" for k, v in shown[:6])
                          + (f", +{len(shown) - 6} more" if len(shown) > 6 else ""),
                          file=sys.stderr)
        if bad:
            print(f"{path}: refusing to emit a plan; {len(bad)} prefab(s) cannot be spawned "
                  f"-- MEASURED absent from the full evidence, not merely absent "
                  f"from a prune:", file=sys.stderr)
            for name, why in sorted(bad.items()):
                print(f"  {name}: {why}", file=sys.stderr)
            print("  drop them with --drop-prefab, or override with "
                  "--allow-missing-prefabs", file=sys.stderr)
            return 2

    yq = yaw_quat(args.rotate) if args.rotate else None
    ox, oy, oz = args.at

    # Yaw about the blueprint's own origin FIRST, then translate so the chosen
    # datum lands on --at. The order matters and it is not symmetric:
    #
    #   * Y is unaffected by a yaw, so the floor datum may be measured either
    #     side of the rotation and is measured before it, as it always was.
    #   * XZ is NOT. An axis-aligned footprint is not rotation-equivariant, and
    #     a body's solids are not centred on its pivot box, so the centre has to
    #     be measured AFTER the yaw. Measuring it before and rotating the offset
    #     with the body leaves the real centre off by `R(yaw)*d - d` -- zero at
    #     0 degrees and `-2d` at 180, i.e. an error that FLIPS SIGN with the
    #     rotation instead of staying put. On `BjOrN_blueprint001` that is
    #     0.35 m of X at yaw 180, and it is 1.2 m of unaccounted footprint on
    #     both axes at every yaw, because a pivot is not a solid.
    ys = [o.pos[1] for o in kept]
    dx = dy = dz = 0.0
    datum = None
    if args.align in ("floor", "floor-center"):
        # The FLOOR PLANE, measured from real piece colliders -- not the pivot
        # plane. `base_geometry` documents why the two are different and what
        # the old min-pivot rule got wrong. `kept` is used rather than the raw
        # body so that a --drop-prefab'd floor cannot define a plane that will
        # not exist in the world.
        datum = base_geometry.floor_datum(kept)
        for v in datum.violations:
            print(f"  datum {v['code']}: {v['detail']}", file=sys.stderr)
        if args.base_y is not None:
            print(f"  datum override: base_y {args.base_y:+.3f} declared by the caller, "
                  f"measured value was "
                  f"{'none' if datum.base_y is None else f'{datum.base_y:+.3f}'}",
                  file=sys.stderr)
            dy = -args.base_y
        elif datum.base_y is None:
            if not args.allow_unplaceable:
                print(f"{path}: refusing to emit a plan; the floor plane cannot be "
                      f"established, so there is no honest Y to place at. Declare one "
                      f"with --base-y, or override with --allow-unplaceable.",
                      file=sys.stderr)
                return 3
            print("  datum UNPLACEABLE and --allow-unplaceable given; using the raw "
                  "origin", file=sys.stderr)
        else:
            dy = -datum.base_y
            print(f"  datum base_y={datum.base_y:+.3f} ({datum.method}) "
                  f"walkable levels={[round(lv.y, 2) for lv in datum.levels[:8]]} "
                  f"terraces above the pad={datum.terraces_m()[:8]}", file=sys.stderr)
    if args.align in ("ground", "ground-center"):
        dy = -min(ys)

    # ----------------------------------------------------------------- #
    # THE EMITTER'S OWN ACCEPTANCE PASS.  Three of the structures gate's
    # refusals are properties of the FILE and are therefore already decided
    # here, before a byte reaches the socket:
    #
    #   * a blueprint that splits into vertically disjoint BODIES, only one
    #     of which can touch the pad.  MEASURED on
    #     `hs_meadows_stone_manor.blueprint`: a 38-piece stone plinth at
    #     local -2.0..-1.0 and a 114-piece timber storey whose lowest solid
    #     is at -0.200, i.e. 0.800 m of air against a 0.781 m limit, with
    #     the door sill 0.990 m above the only floor under it.  It reached
    #     five sites on this world -- stenvik-manor-1, stenvik-stonehouse-1
    #     and -2, vestvik-stonehouse-1 and -2 -- and `floor_datum` reported
    #     NO violation, because `BaseProfile` keeps the lowest solid per
    #     column and a silhouette cannot see a void above it.
    #   * inverted oriented pieces.  Same three files, 4 / 2 / 2, which are
    #     exactly the counts the gate later found standing in the world.
    #   * two rows of one prefab on one pivot: two ZDOs, one of them
    #     invisible.
    #
    # `preflight` asks the GATE'S OWN instruments, not a second opinion.
    # Each refusal names the flag that overrides it, and an overridden
    # refusal is still printed with its numbers, because the failure this
    # replaces was silence and not disagreement.
    base_for_pf = (args.base_y if args.base_y is not None
                   else (datum.base_y if datum is not None else None))

    def measure(rows_now):
        return preflight.preflight(rows_now, base_for_pf,
                                   spawnable=spawnable_name,
                                   yaw_deg=args.rotate)

    pf_refusals, pf_report = measure(kept)

    # THE REPAIRS, applied before the refusal is decided, because a repaired
    # defect is not a defect and a refusal that survives one is the honest
    # answer.  All are re-measured afterwards: the acceptance for a repair
    # is the check coming out clean, never the claim that it ran.
    #
    # EVERY ROW A REPAIR TOUCHES IS RECORDED BY NAME, on stderr and in
    # OUT.repairs.json next to the plan - never as a count.  A repair changes
    # what the file authored, and `settlements/build.py` keeps only the plan
    # it emits, so the record has to travel with the plan or it does not
    # exist.  `row` is the row's index in the blueprint as read, which with
    # the prefab and the local position is the durable key for it.
    file_row = {id(o): i for i, o in enumerate(objs)}

    def local(o) -> list[float]:
        return [round(float(v), 4) for v in o.pos]

    record: dict = {"blueprint": path.name, "base_y": base_for_pf,
                    "repairs": {"drop_co_located_copies":
                                    args.drop_co_located_copies,
                                "reorient_upside_down_panels":
                                    args.reorient_upside_down_panels,
                                "upright_panels": args.upright_panels,
                                "seat_unsupported_bodies":
                                    args.seat_unsupported_bodies,
                                "seat_sunk_props": args.seat_sunk_props,
                                "drop_subgrade_rows": args.drop_subgrade_rows,
                                "reseat_blocked_beds":
                                    args.reseat_blocked_beds},
                    "copies_dropped": [], "reoriented": [],
                    "left_upside_down": [], "yawed": [], "yaw_uncured": [],
                    "seated": [], "props_seated": [], "props_left": [],
                    "dropped": [], "reseated": [], "seats_left": [],
                    # EVERY claimable bed as authored, by the gate's probe,
                    # so a seat that passes is on the page beside one that
                    # was moved - a repair cannot be diffed otherwise.
                    "spawn_seats_authored": [
                        dict(s, row=file_row[id(kept[s["row"]])])
                        for s in pf_report["spawn_seats"]]}
    authored = materialise.authored_repair(path)
    if authored is not None:
        addition = materialise.AUTHORED_REPAIRS.parent / authored["addition"]
        added = _read_objects(addition)
        record["authored_addition"] = {
            "path": str(addition), "sha256": materialise.sha256(addition),
            "source_sha256": materialise.sha256(path),
            "rows": [{"row": len(objs) - len(added) + i,
                      "prefab": o.prefab, "local": local(o)}
                     for i, o in enumerate(added)]}
        for row in record["authored_addition"]["rows"]:
            print(f"  authored ADDITION row {row['row']} {row['prefab']} "
                  f"at local {row['local']} from {addition.name}",
                  file=sys.stderr)
        record["authored_poses"] = authored.get("poses", [])
        for pose in record["authored_poses"]:
            print(f"  authored POSE row {pose['row']} {pose['prefab']} "
                  f"at local {pose['pos']}: {pose['from_rot']} -> "
                  f"{pose['to_rot']}", file=sys.stderr)

    # CO-LOCATED COPIES FIRST: a copy is a property of the file alone, and an
    # inverted pair left in would be judged, and yawed, twice.
    if args.drop_co_located_copies and pf_report["copies_plan"]:
        for r in pf_report["copies_plan"]:
            o = kept[r["row"]]
            rec = {"row": file_row[id(o)], "prefab": o.prefab,
                   "local": local(o),
                   "inside_rows": [file_row[id(kept[j])]
                                   for j in r["inside_rows"]],
                   "buried_fraction": r["buried_fraction"]}
            record["copies_dropped"].append(rec)
            print(f"  preflight DROPPED co-located row {rec['row']} "
                  f"{rec['prefab']} at local ({rec['local'][0]:.3f},"
                  f"{rec['local'][1]:.3f},{rec['local'][2]:.3f}): "
                  f"{rec['buried_fraction']:.0%} inside kept row(s) "
                  f"{rec['inside_rows']} of the same prefab", file=sys.stderr)
        gone = {r["row"] for r in pf_report["copies_plan"]}
        kept = [o for i, o in enumerate(kept) if i not in gone]
        dropped["co_located_copy"] = len(gone)
        print(f"  preflight REPAIRED {len(gone)} co-located copy row(s) "
              f"removed, each named above; a second ZDO in the same solid "
              f"is not a piece", file=sys.stderr)
        pf_refusals, pf_report = measure(kept)

    repaired = []
    if args.reorient_upside_down_panels and pf_report["reorient_plan"]:
        turned = 0
        for r in pf_report["reorient_plan"]:
            o = kept[r["row"]]
            rec = {"row": file_row[id(o)], "prefab": o.prefab,
                   "local": local(o)}
            if r["status"] != "reposed":
                rec.update(why=r["why"], margin_m=r.get("margin_m"))
                record["left_upside_down"].append(rec)
                print(f"  preflight UPSIDE DOWN row {rec['row']} "
                      f"{rec['prefab']} at local ({rec['local'][0]:.3f},"
                      f"{rec['local'][1]:.3f},{rec['local'][2]:.3f}) left as "
                      f"authored: {r['why']}", file=sys.stderr)
                continue
            o.prefab = r["to_prefab"]
            o.pos = tuple(r["pos"])
            o.rot = tuple(r["rot"])
            rec.update(to_prefab=o.prefab, to_local=local(o),
                       rot=list(o.rot),
                       solid_deviation_m=r["solid_deviation_m"])
            record["reoriented"].append(rec)
            turned += 1
            print(f"  preflight REORIENTED row {rec['row']} {rec['prefab']} "
                  f"at local ({rec['local'][0]:.3f},{rec['local'][1]:.3f},"
                  f"{rec['local'][2]:.3f}) -> {o.prefab} at local "
                  f"({rec['to_local'][0]:.3f},{rec['to_local'][1]:.3f},"
                  f"{rec['to_local'][2]:.3f}): the same solid within "
                  f"{rec['solid_deviation_m']:.4f} m, now the right way up",
                  file=sys.stderr)
        if turned:
            repaired.append(f"{turned} upside-down panel(s) turned the right "
                            f"way up in their own solid, each named above")
    yawed_from: dict[int, float] = {}
    flip = yaw_quat(180.0)

    def yaw_rows(plan) -> None:
        for row in plan:
            o = kept[row["row"]]
            o.rot = qmul(flip, o.rot)
            yawed_from[id(o)] = row["toward"]

    if args.upright_panels and pf_report["upright_plan"]:
        yaw_rows(pf_report["upright_plan"])
    if args.seat_unsupported_bodies and pf_report["seat_plan"]:
        for b in pf_report["seat_plan"]:
            for i in b["rows"]:
                o = kept[i]
                o.pos = (o.pos[0], o.pos[1] - b["drop_m"], o.pos[2])
            record["seated"].append({
                "drop_m": b["drop_m"], "pieces": b["pieces"],
                "first_contact": b["lowest_piece"],
                "rows": sorted(file_row[id(kept[i])] for i in b["rows"])})
            repaired.append(f"body of {b['pieces']} piece(s) lowered "
                            f"{b['drop_m']:.4f} m onto what is under it "
                            f"(first contact: {b['lowest_piece']})")
    if repaired or yawed_from:
        pf_refusals, pf_report = measure(kept)

    # THE YAW IS ASKED AGAIN, because a yaw moves a panel's AABB and so the
    # ridge the instrument measures every other panel against.  A row the
    # first pass exposed is yawed on the next; a row is NEVER yawed twice,
    # because the second yaw is the inverse of the first.  A row still
    # inverted after its own yaw was not cured by it - MEASURED on Fresh42's
    # sandbox-portal-hub, four `darkwood_roof_ocorner` whose high corners meet
    # within 0.09 m of their common apex read -0.947 authored AND -0.947
    # yawed - so it is RESTORED as authored and named with both margins: a
    # repair that does not cure is not applied.
    if args.upright_panels:
        for _ in range(len(kept)):
            fresh = [r for r in pf_report["upright_plan"]
                     if id(kept[r["row"]]) not in yawed_from]
            if not fresh:
                break
            yaw_rows(fresh)
            pf_refusals, pf_report = measure(kept)
        stale = [r for r in pf_report["upright_plan"]
                 if id(kept[r["row"]]) in yawed_from]
        for r in stale:
            o = kept[r["row"]]
            o.rot = qmul(flip, o.rot)
            rec = {"row": file_row[id(o)], "prefab": o.prefab,
                   "local": local(o), "authored_margin_m":
                       yawed_from.pop(id(o)), "yawed_margin_m": r["toward"]}
            record["yaw_uncured"].append(rec)
            print(f"  preflight UNCURED row {rec['row']} {rec['prefab']} at "
                  f"local ({rec['local'][0]:.3f},{rec['local'][1]:.3f},"
                  f"{rec['local'][2]:.3f}): rises away "
                  f"{rec['authored_margin_m']:+.3f} m as authored and "
                  f"{rec['yawed_margin_m']:+.3f} m yawed 180; restored as "
                  f"authored", file=sys.stderr)
        if stale:
            pf_refusals, pf_report = measure(kept)
        for o in kept:
            if id(o) in yawed_from:
                record["yawed"].append({"row": file_row[id(o)],
                                        "prefab": o.prefab,
                                        "local": local(o)})
        if record["yawed"]:
            repaired.insert(0, f"{len(record['yawed'])} panel(s) yawed 180 "
                               f"degrees onto their own ridge")
    # SUNK PROPS, on the geometry every body-level repair has settled: the
    # lift is measured against the plane the datum chose and the solids that
    # will actually stand around the prop.
    if args.seat_sunk_props and pf_report["props_plan"]:
        lifted = 0
        for r in pf_report["props_plan"]:
            o = kept[r["row"]]
            rec = {"row": file_row[id(o)], "prefab": o.prefab,
                   "local": local(o), "depth_m": r["depth_m"]}
            if r["status"] != "seated":
                rec.update(why=r["why"], enters=r.get("enters", []))
                record["props_left"].append(rec)
                print(f"  preflight SUNK prop row {rec['row']} {rec['prefab']} "
                      f"at local ({rec['local'][0]:.3f},{rec['local'][1]:.3f},"
                      f"{rec['local'][2]:.3f}) left {rec['depth_m']:.3f} m "
                      f"under the floor plane: {r['why']}", file=sys.stderr)
                continue
            o.pos = (o.pos[0], o.pos[1] + r["lift_m"], o.pos[2])
            record["props_seated"].append(rec)
            lifted += 1
            print(f"  preflight SEATED prop row {rec['row']} {rec['prefab']} "
                  f"at local ({rec['local'][0]:.3f},{rec['local'][1]:.3f},"
                  f"{rec['local'][2]:.3f}) lifted {r['lift_m']:.3f} m onto "
                  f"the floor plane; it enters no other physical solid there",
                  file=sys.stderr)
        if lifted:
            repaired.append(f"{lifted} sunk prop(s) lifted onto the floor "
                            f"plane, each named above")
            pf_refusals, pf_report = measure(kept)
    for line in repaired:
        print(f"  preflight REPAIRED {line}", file=sys.stderr)

    # DROPPING ROWS COMES LAST AND ITERATES, because it is the only repair
    # that changes the row set, and because SEATING a body can push its own
    # lowest course under the pad - so the two repairs can feed each other.
    # The loop is bounded: each pass removes at least one row or stops.  It
    # does NOT run while `subgrade_carried` names a kept row that a sub-grade
    # row holds up: that drop is refused, and `subgrade_structural` says why.
    if args.drop_subgrade_rows:
        while (pf_report["subgrade_plan"]
               and not pf_report["subgrade_carried"]):
            plan = pf_report["subgrade_plan"]
            for r in plan:
                o = kept[r["row"]]
                rec = {"row": file_row[id(o)], "prefab": o.prefab,
                       "local": local(o), "own_top": r["own_top"],
                       "depth_m": r["depth_m"]}
                record["dropped"].append(rec)
                print(f"  preflight DROPPED row {rec['row']} {rec['prefab']} "
                      f"at local ({rec['local'][0]:.3f},{rec['local'][1]:.3f},"
                      f"{rec['local'][2]:.3f}), own top {rec['own_top']:+.3f}"
                      f" - {rec['depth_m']:.3f} m under the pad plane",
                      file=sys.stderr)
            gone = {r["row"] for r in plan}
            kept = [o for i, o in enumerate(kept) if i not in gone]
            pf_refusals, pf_report = measure(kept)
        if record["dropped"]:
            dropped["subgrade"] = len(record["dropped"])
            print(f"  preflight REPAIRED {len(record['dropped'])} row(s) "
                  f"removed as wholly below the pad plane, each named above; "
                  f"an invisible piece is not a piece", file=sys.stderr)

    # SPAWN SEATS LAST, on the geometry every other repair has settled: a
    # bed's room is whatever the kept rows make it.  One pass, then the
    # re-measure is the acceptance - a seat still refused is a refusal.
    if args.reseat_blocked_beds and pf_report["reseat_plan"]:
        for e in preflight.apply_reseat(kept, pf_report["reseat_plan"]):
            rec = {"row": file_row[id(kept[e["row"]])], "prefab": e["prefab"],
                   "local": e["from"],
                   "to_local": [round(float(v), 4) for v in e["to"]],
                   "turn_deg": e["turn_deg"], "moved_m": e["moved_m"],
                   "before": e["before"], "after": e["after"]}
            record["reseated"].append(rec)
            print(f"  preflight RESEATED bed row {rec['row']} {rec['prefab']} "
                  f"local ({rec['local'][0]:.3f},{rec['local'][1]:.3f},"
                  f"{rec['local'][2]:.3f}) -> ({rec['to_local'][0]:.3f},"
                  f"{rec['to_local'][1]:.3f},{rec['to_local'][2]:.3f}), "
                  f"{rec['moved_m']:.3f} m, turned {rec['turn_deg']:+.1f} deg:"
                  f" room {e['before']['clearance_m']:.3f} -> "
                  f"{e['after']['clearance_m']:.3f} m, headroom "
                  f"{e['before']['headroom_m']:.3f} -> "
                  f"{e['after']['headroom_m']:.3f} m", file=sys.stderr)
        for e in pf_report["reseat_plan"]:
            if e.get("status") != "reseated":
                record["seats_left"].append(
                    {"row": file_row[id(kept[e["row"]])],
                     "prefab": e["prefab"], "local": e["from"],
                     "before": e["before"], "why": e["why"]})
        if record["reseated"]:
            print(f"  preflight REPAIRED {len(record['reseated'])} spawn "
                  f"seat(s) moved inside their own room, each named above",
                  file=sys.stderr)
            pf_refusals, pf_report = measure(kept)
    record["spawn_seats"] = [dict(s, row=file_row[id(kept[s["row"]])])
                             for s in pf_report["spawn_seats"]]

    allowed = {
        "--allow-unsupported-bodies": args.allow_unsupported_bodies,
        "--allow-inverted-panels": args.allow_inverted_panels,
        "--allow-co-located-rows": args.allow_co_located_rows,
        "--allow-unsheltered-deck": args.allow_unsheltered_deck,
        "--allow-roof-skin-gaps": args.allow_roof_skin_gaps,
        "--allow-subgrade-rows": args.allow_subgrade_rows,
        "--allow-blocked-seats": args.allow_blocked_seats,
    }
    blocking = [r for r in pf_refusals if not allowed.get(r.override)]

    def keep_record(status: str) -> None:
        if not args.out:
            return
        record["status"] = status
        record["refusals"] = [{"code": r.code, "override": r.override,
                               "blocking": r in blocking, **r.numbers}
                              for r in pf_refusals]
        Path(f"{args.out}.repairs.json").write_text(
            json.dumps(record, indent=1, default=str) + "\n", "utf-8")

    for r in pf_refusals:
        # A REFUSED line carries its own override flag, so the caller that
        # PARSES this stream -- `settlements/spawnable.py` does -- can name
        # the remedy this refusal actually has instead of guessing one from a
        # list. An overridden refusal already named its flag; a blocking one
        # did not, and that asymmetry is what let a downstream reader print a
        # remedy belonging to a different refusal.
        state = (f"REFUSED (override {r.override})" if r in blocking
                 else f"allowed by {r.override}")
        print(f"  preflight {state} {r}", file=sys.stderr)
    for s in pf_report["skipped"]:
        print(f"  preflight skipped {s}", file=sys.stderr)
    if blocking:
        off = [f"--no-{k.replace('_', '-')}"
               for k, on in record["repairs"].items() if not on]
        print(f"{path}: refusing to emit a plan; {len(blocking)} preflight "
              f"refusal(s) above ({', '.join(r.code for r in blocking)}). "
              f"Each names the flag that overrides it, and each SURVIVED "
              f"the emitter's deterministic repairs"
              + (f" - except that {', '.join(off)} switched some of them "
                 f"off" if off else "")
              + ". Emitting a defect writes it into the world, where the "
              f"acceptance gate refuses it afterwards at the cost of a "
              f"retire-and-replace instead of an edit.", file=sys.stderr)
        keep_record("refused")
        return 4
    foot = base_geometry.xz_footprint(kept, args.rotate)
    if args.align in ("center", "ground-center", "floor-center"):
        dx = -foot.center_x
        dz = -foot.center_z
    if foot.pivot_only:
        names = sorted(set(foot.pivot_only))
        print(f"  footprint: {len(foot.pivot_only)} row(s) across {len(names)} prefab(s) "
              f"have no measured solid and contributed their pivot only: "
              f"{', '.join(names[:6])}", file=sys.stderr)
    print(
        f"  align={args.align} origin shift=({dx:+.2f},{dy:+.2f},{dz:+.2f})"
        f" footprint@yaw{args.rotate:g}={foot.size_x:.2f}x{foot.size_z:.2f}"
        f" height={max(ys) - min(ys):.1f}",
        file=sys.stderr,
    )
    print(
        f"  expected world span x {foot.min_x + dx + ox:.2f}..{foot.max_x + dx + ox:.2f}"
        f" z {foot.min_z + dz + oz:.2f}..{foot.max_z + dz + oz:.2f}"
        f" centre ({foot.center_x + dx + ox:.2f},{foot.center_z + dz + oz:.2f})",
        file=sys.stderr,
    )

    # Bottom-up so WearNTear support exists below each piece as it appears.
    kept.sort(key=lambda o: o.pos[1])

    worst = 0.0
    how: dict[str, int] = {}
    scale_unrepeated = 0
    inventories = 0
    lines: list[str] = []
    for o in kept:
        pos = o.pos
        rot = o.rot
        if yq is not None:
            pos = qrot(yq, pos)
            rot = qmul(yq, rot)
        pos = (pos[0] + dx, pos[1] + dy, pos[2] + dz)
        ex, ey, ez = quat_to_euler(*rot)
        if args.verify:
            back = euler_to_quat(ex, ey, ez)
            worst = max(worst, quat_angle_between(rot, back))
        if args.no_data:
            blob, kind, entry = "", NONE, None
        else:
            blob, kind, entry = data_for(o, strip_identity=args.strip_identity,
                                         merge_scale=args.merge_scale)
        how[kind] = how.get(kind, 0) + 1
        if entry is not None:
            if (kind == PASS_THROUGH
                    and any(abs(s - 1.0) > 1e-4 for s in o.scale)
                    and data_entry.stable_hash("scale") not in entry.vecs):
                scale_unrepeated += 1
            if data_entry.inventory_of(entry) is not None:
                inventories += 1
        cmd = (
            f"spawn_object {o.prefab}"
            f" pos={pos_arg(pos[0] + ox, pos[1] + oy, pos[2] + oz)}"
            f" rot={rot_arg(ex, ey, ez)}"
            f" {FROM_ORIGIN}"
        )
        if blob:
            cmd += f" data={blob}"
        lines.append(cmd)

    body = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(body, "utf-8")
        keep_record("emitted")
    else:
        sys.stdout.write(body)

    note = sys.stderr
    print(f"{path.name}: {len(objs)} objects read, {len(lines)} spawn_object commands",
          file=note)
    print(
        "  dropped: "
        + ", ".join(f"{k}={v}" for k, v in dropped.items() if v)
        + (" (none)" if not any(dropped.values()) else ""),
        file=note,
    )
    print("  data: " + ", ".join(f"{k}={v}" for k, v in sorted(how.items()) if v),
          file=note)
    print(f"  container inventories carried: {inventories}", file=note)
    if scale_unrepeated:
        print(f"  NOTE: {scale_unrepeated} object(s) state a non-unit scale in columns "
              f"10-12 that their own payload does not repeat; the payload is passed "
              f"through verbatim, so that scale is NOT applied. --merge-scale adds it.",
              file=note)
    if args.verify:
        print(f"  max rotation round-trip error: {worst:.6f} deg", file=note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
