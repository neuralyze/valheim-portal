#!/usr/bin/env python3
"""Translate a PlanBuild-format blueprint into a ValheimRcon `spawn` plan.

Why this exists
---------------
Infinity Hammer's blueprint commands (`hammer_blueprint`, `hammer_restore`,
`hammer_save`) are **admin-client only**: MEASURED, `hammer_restore` calls
`InfinityHammer.Hammer::Equip()`, which calls
`ServerDevcommands.Helper::GetPlayer()` and then `Humanoid::EquipItem`, so it
needs a live local player holding a hammer.  There is no headless path through
Infinity Hammer.

ValheimRcon's `spawn` **is** headless -- MEASURED, it resolves the prefab from
`ZNetScene`, instantiates it, and closes with `ZNetView::FinishGhostInit()` plus
`Object::Destroy`, i.e. it writes a persistent ZDO without needing a player in
the world.  So this tool converts the high-fidelity client-side format into the
low-fidelity headless channel.

What is lost, exactly
---------------------
`spawn <prefab> <x> <y> <z> -rotation <x> <y> <z>` takes **euler degrees**
(MEASURED: `Quaternion::Euler(Vector3)`), and offers no scale and no object-data
argument.  Therefore:

  * rotation  -- preserved.  Quaternion -> Unity euler -> quaternion is exact
                 for the rotation itself; `--verify` proves it per blueprint.
  * scale     -- LOST.  Reported as a count; it is a handful of pieces at most
                 in every real blueprint measured here.
  * data      -- LOST.  Chest contents, sign text, item-stand contents and
                 ward permissions live in the blueprint `data` column and have
                 no `spawn` equivalent.
  * creator   -- LOST.  `spawn` never sets `Piece.m_creator`, so pieces land
                 with creator 0.
  * support   -- NOT COMPUTED.  Ghost-init writes the ZDO without running
                 WearNTear support propagation, so an unsupported piece can be
                 destroyed when the zone first loads.  Emit in bottom-up Y
                 order (the default here) to keep each piece's support below it
                 already placed.

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
response over 4050 payload bytes, and `spawn` echoes a detailed line per
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
    # Hammer's own state machine. Anything not under `#Pieces` is not an object,
    # and this converter's whole output is `spawn` commands aimed at a live
    # server -- a converter that can emit `spawn circle` is a converter that will
    # eventually be run.
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
            continue
        if section != "#pieces":
            continue
        line = row.replace(",", ".") if "," in row else row
        p = line.split(";")
        name = p[0].strip()
        if not name:
            continue
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
        default="ground-center",
        choices=("raw", "ground", "center", "ground-center"),
        help=(
            "how the blueprint's own origin maps onto --at. Blueprints in this corpus "
            "disagree: some are corner-origin with min at (0,0,0), some are centred, and "
            "their lowest piece sits anywhere from -42 to 0 on Y. 'ground' drops the "
            "lowest piece exactly onto --at Y; 'center' puts the XZ bounding-box centre "
            "on --at XZ; 'ground-center' (default) does both; 'raw' trusts the file."
        ),
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
    if args.align in ("ground", "ground-center"):
        dy = -min(ys)
    if args.align in ("center", "ground-center"):
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
    scaled = 0
    with_data = 0
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
        if any(abs(s - 1.0) > 1e-4 for s in o.scale):
            scaled += 1
        if o.data.strip():
            with_data += 1
        cmd = (
            f"spawn {o.prefab} {fmt(pos[0] + ox)} {fmt(pos[1] + oy)} {fmt(pos[2] + oz)}"
            f" -rotation {fmt(ex)} {fmt(ey)} {fmt(ez)}"
        )
        lines.append(cmd)

    body = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(body, "utf-8")
    else:
        sys.stdout.write(body)

    note = sys.stderr
    print(f"{path.name}: {len(objs)} objects read, {len(lines)} spawn commands", file=note)
    print(
        "  dropped: "
        + ", ".join(f"{k}={v}" for k, v in dropped.items() if v)
        + (" (none)" if not any(dropped.values()) else ""),
        file=note,
    )
    print(f"  scale lost on {scaled} objects; object data lost on {with_data}", file=note)
    if args.verify:
        print(f"  max rotation round-trip error: {worst:.6f} deg", file=note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
