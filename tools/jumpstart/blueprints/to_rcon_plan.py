#!/usr/bin/env python3
"""Translate a PlanBuild-format blueprint into a ValheimRcon `spawn_object` plan.

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

The plan is one RCON command per line.  Feed it to an RCON client one command
at a time and keep each response small: MEASURED, ValheimRcon truncates any
response over 4050 payload bytes, and `spawn_object` echoes a detailed line per
spawned object, so batching with `-count` is a bad idea.
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

RAD = 180.0 / math.pi


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
    """Read every object out of a .blueprint or .vbuild, full fidelity.

    Field layouts are documented in `inventory.py` and were both MEASURED from
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
            "puts the XZ bounding-box centre on --at XZ and the blueprint's FLOOR PLANE "
            "on --at Y, where the floor plane is measured by base_geometry.floor_datum "
            "from real piece colliders; 'floor' does the Y half only. 'ground' and "
            "'ground-center' are the OLD rule, min pivot Y, kept only for comparison: "
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

    drop = set(args.drop_prefab)
    dropped = {"loot": 0, "creature": 0, "forage": 0, "explicit": 0}
    kept: list[Obj] = []
    for o in objs:
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
    if not args.allow_missing_prefabs:
        ev = Path(args.evidence)
        if not ev.exists():
            print(f"{ev}: no prefab evidence; pass --allow-missing-prefabs to skip the check",
                  file=sys.stderr)
            return 2
        resolution = json.loads(ev.read_text("utf-8"))["resolution"]
        bad: dict[str, str] = {}
        for o in kept:
            verdict = resolution.get(o.prefab)
            if verdict is None:
                bad[o.prefab] = "not in the evidence file"
            elif verdict == "MISSING":
                bad[o.prefab] = "MISSING from every bundle and deployed mod"
        if bad:
            print(f"{path}: refusing to emit a plan; {len(bad)} prefab(s) cannot be spawned:",
                  file=sys.stderr)
            for name, why in sorted(bad.items()):
                print(f"  {name}: {why}", file=sys.stderr)
            print("  drop them with --drop-prefab, or override with "
                  "--allow-missing-prefabs", file=sys.stderr)
            return 2

    yq = yaw_quat(args.rotate) if args.rotate else None
    ox, oy, oz = args.at

    # Normalise the blueprint's own origin, then yaw, then translate onto --at.
    # The yaw has to happen about the aligned origin, otherwise rotating a
    # corner-origin blueprint swings the whole building off the site.
    xs = [o.pos[0] for o in kept]
    ys = [o.pos[1] for o in kept]
    zs = [o.pos[2] for o in kept]
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
    if args.align in ("center", "ground-center", "floor-center"):
        dx = -(min(xs) + max(xs)) * 0.5
        dz = -(min(zs) + max(zs)) * 0.5
    print(
        f"  align={args.align} origin shift=({dx:+.2f},{dy:+.2f},{dz:+.2f})"
        f" extent={max(xs) - min(xs):.1f}x{max(zs) - min(zs):.1f}x{max(ys) - min(ys):.1f}",
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
        pos = (o.pos[0] + dx, o.pos[1] + dy, o.pos[2] + dz)
        rot = o.rot
        if yq is not None:
            pos = qrot(yq, pos)
            rot = qmul(yq, rot)
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
