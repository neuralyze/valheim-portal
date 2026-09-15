#!/usr/bin/env python3
"""Tests for blueprint XZ centring under rotation -- `to_rcon_plan.py --align
*-center` and `base_geometry.xz_footprint`.

The defect, MEASURED on Ulfsland's `iron-era-workshop` site: the emitter chose
its XZ centre from the PIVOT bounding box, in blueprint-local space, and then
yawed the whole body about that centre. Two things are wrong with that, and only
the second one is visible at a yaw of 0:

  1. A pivot is not a solid. `BjOrN_blueprint001`'s pivot box is
     `0..65.467` x `0..65.984`; its measured colliders reach `-0.63..65.74` x
     `-0.63..66.55`. The pivot box therefore names a centre `(+0.17, +0.03)` m
     away from the real one and understates the body by ~1.2 m on both axes.

  2. That offset was then ROTATED WITH THE BODY. Call it `d`. Centring on the
     pivot box pins the pivot-box centre to `--at`, so the real centre lands at
     `--at + R(yaw) * d`: `+d` at yaw 0, and `-d` at yaw 180. The error FLIPS
     SIGN with the rotation rather than staying put, which is what "centred at 0
     and off in the opposite direction at 180" looks like from inside the world.

An axis-aligned bounding box is not rotation-equivariant, so the centre has to
be measured AFTER the yaw. `xz_footprint(objects, yaw)` does that, from collider
corners, and the emitter applies its translation after the rotation.

Why the fixtures are LOPSIDED: at yaws that are multiples of 90 degrees an AABB
is equivariant, so for a body whose solids are centred on its pivot box the two
orders agree exactly and a symmetric fixture passes under the bug. These
fixtures put a 4 x 4 m slab at one corner and two 1 x 1 m tiles 20 m away, so
the solid box centre sits 0.75 m from the pivot box centre on both axes and the
sign flip is 1.5 m -- thirty times the tolerance asserted below.

Everything here is offline: synthetic blueprints in a temp dir, the committed
collider dump, no server.
"""

import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BLUEPRINTS = TOOLS / "jumpstart" / "blueprints"
sys.path.insert(0, str(BLUEPRINTS))

import base_geometry as bg  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402

HEADER = """#Name:{name}
#Creator:test
#Description:fixture
#Category:test
#Pieces
"""

# Prefab-local XZ collider boxes, (min_x, max_x, min_z, max_z), asserted against
# the committed dump by `MeasuredFixtureGeometry` below. Hard-coded here on
# purpose: the expected world extent of a plan must not be computed with the
# same code that produced the plan.
BOX = {
    "stone_floor": (-2.0, 2.0, -2.0, 2.0),
    "wood_floor_1x1": (-0.5096, 0.4904, -0.5148, 0.4852),
}

# Y offsets that put both floor types' walkable TOP at local +0.5, so the body
# has exactly one walkable level and the datum is unambiguous.
#   stone_floor    solid Y -0.5..+0.5  -> pivot 0.0
#   wood_floor_1x1 solid Y -0.033..+0.097 -> pivot 0.4031
PIVOT_Y = {"stone_floor": 0.0, "wood_floor_1x1": 0.4031}
LEVEL_TOP = 0.5

# The lopsided body. One 4 x 4 slab at the local origin, two 1 x 1 tiles 20 m
# out along +X and +Z.
LOPSIDED = (
    ("stone_floor", 0.0, 0.0),
    ("wood_floor_1x1", 20.0, 0.0),
    ("wood_floor_1x1", 0.0, 20.0),
)

# Derived by hand from BOX and LOPSIDED, unrotated:
#   pivot box   x 0.0 .. 20.0        centre 10.0
#   solid box   x -2.0 .. 20.4904    centre  9.2452
#   solid box   z -2.0 .. 20.4852    centre  9.2426
# so the pivot box names a centre 0.7548 m / 0.7574 m away from the real one,
# and the old order's error at yaw 180 is twice that.
PIVOT_CENTRE = (10.0, 10.0)
SOLID_CENTRE = (9.2452, 9.2426)

PAD = 70.91
AT_X, AT_Z = -4673.5, -310.5

# Tolerance on a centre. The emitter writes 4 decimal places and rounds
# trailing zeroes off, so 0.05 m is four orders of magnitude above the
# representation error and forty times below the defect it is here to catch.
TOL_M = 0.05


def row(prefab: str, x: float, y: float, z: float) -> str:
    return f"{prefab};;{x};{y};{z};0;0;0;1;;1;1;1;"


def blueprint(tmp: Path, name: str, rows) -> Path:
    path = tmp / f"{name}.blueprint"
    path.write_text(HEADER.format(name=name) + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def lopsided(tmp: Path, name: str = "lopsided") -> Path:
    return blueprint(tmp, name, [row(p, x, PIVOT_Y[p], z) for p, x, z in LOPSIDED])


def yaw_xz(x: float, z: float, deg: float) -> tuple[float, float]:
    """The emitter's yaw, applied to an XZ point.

    `to_rcon_plan.yaw_quat` builds `(0, sin(d/2), 0, cos(d/2))` and `qrot`
    applies it, which reduces to this; the equivalence is checked to 1e-13 in
    `test_the_local_yaw_matches_the_emitters_quaternion`.
    """
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return x * c + z * s, z * c - x * s


class PlanBox:
    """The XZ box a plan's objects occupy, and the heights they sit at.

    Rebuilt from the emitted `spawn_object` text alone, using the hard-coded
    `BOX` extents: this is the measurement, so it must not call back into
    `base_geometry`.
    """

    def __init__(self, out: Path):
        self.min_x = self.min_z = math.inf
        self.max_x = self.max_z = -math.inf
        self.rows = 0
        self.pivot_y: dict[str, float] = {}
        for line in out.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split(" ")
            prefab = parts[1]
            pos = rot = None
            for token in parts:
                if token.startswith("pos="):
                    pos = [float(v) for v in token[4:].split(",")]
                elif token.startswith("rot="):
                    rot = [float(v) for v in token[4:].split(",")]
            # `pos=` is z,x,y and `rot=` is euler y,x,z -- both orders are
            # documented on `pos_arg` / `rot_arg` and were read out of the IL.
            wz, wx, wy = pos
            yaw = rot[0]
            self.rows += 1
            self.pivot_y[prefab] = wy
            lo_x, hi_x, lo_z, hi_z = BOX[prefab]
            for cx in (lo_x, hi_x):
                for cz in (lo_z, hi_z):
                    dx, dz = yaw_xz(cx, cz, yaw)
                    self.min_x = min(self.min_x, wx + dx)
                    self.max_x = max(self.max_x, wx + dx)
                    self.min_z = min(self.min_z, wz + dz)
                    self.max_z = max(self.max_z, wz + dz)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) * 0.5, (self.min_z + self.max_z) * 0.5)

    @property
    def size(self) -> tuple[float, float]:
        return (self.max_x - self.min_x, self.max_z - self.min_z)


class MeasuredFixtureGeometry(unittest.TestCase):
    """The collider extents the fixtures' arithmetic is built on. A bad
    regeneration of `data/piece_geometry.json` fails here rather than turning
    the centring assertions into nonsense."""

    @classmethod
    def setUpClass(cls):
        cls.g = bg.geometry()

    def test_fixture_prefab_boxes_match_the_committed_dump(self):
        for prefab, expected in BOX.items():
            with self.subTest(prefab=prefab):
                box = self.g.local_aabb(prefab)
                self.assertIsNotNone(box, prefab)
                got = (box[0], box[3], box[2], box[5])
                for a, b in zip(got, expected):
                    self.assertAlmostEqual(a, b, places=4, msg=prefab)

    def test_the_fixture_is_actually_lopsided(self):
        # If this ever became symmetric every centring test below would pass
        # under the bug, so the asymmetry is asserted rather than assumed.
        self.assertGreater(abs(PIVOT_CENTRE[0] - SOLID_CENTRE[0]), 0.5)
        self.assertGreater(abs(PIVOT_CENTRE[1] - SOLID_CENTRE[1]), 0.5)

    def test_both_fixture_floors_put_their_walkable_top_at_the_same_level(self):
        for prefab, pivot in PIVOT_Y.items():
            with self.subTest(prefab=prefab):
                span = self.g.y_span(prefab, (0, 0, 0, 1), (1, 1, 1))
                self.assertAlmostEqual(pivot + span[1], LEVEL_TOP, places=3)

    def test_the_local_yaw_matches_the_emitters_quaternion(self):
        from to_rcon_plan import qrot, yaw_quat

        for deg in (0.0, 37.5, 90.0, 180.0, 270.0, -145.0):
            with self.subTest(deg=deg):
                ref = qrot(yaw_quat(deg), (13.0, 4.0, -7.0))
                got = yaw_xz(13.0, -7.0, deg)
                self.assertAlmostEqual(got[0], ref[0], places=9)
                self.assertAlmostEqual(got[1], ref[2], places=9)


class FootprintUnit(unittest.TestCase):
    """`base_geometry.xz_footprint` on its own."""

    @classmethod
    def setUpClass(cls):
        cls.g = bg.geometry()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.objs = read_objects(lopsided(cls.tmp, "unit"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_footprint_is_the_solid_box_not_the_pivot_box(self):
        foot = bg.xz_footprint(self.objs, 0.0, self.g)
        self.assertAlmostEqual(foot.min_x, -2.0, places=3)
        self.assertAlmostEqual(foot.max_x, 20.4904, places=3)
        self.assertAlmostEqual(foot.center_x, SOLID_CENTRE[0], places=3)
        self.assertAlmostEqual(foot.center_z, SOLID_CENTRE[1], places=3)
        # and it is NOT the pivot box, which is what the emitter used to use
        self.assertNotAlmostEqual(foot.center_x, PIVOT_CENTRE[0], places=1)

    def test_the_footprint_centre_tracks_the_yaw(self):
        # The centre of a rotated body is the rotation of its own centre only
        # when the box is equivariant. Here the yawed footprint centre must
        # equal the yaw applied to the unyawed centre at multiples of 90, and
        # must NOT at 45 -- the property that makes measuring after the rotation
        # the only correct order.
        base = bg.xz_footprint(self.objs, 0.0, self.g)
        for deg in (90.0, 180.0, 270.0):
            with self.subTest(deg=deg):
                foot = bg.xz_footprint(self.objs, deg, self.g)
                want = yaw_xz(base.center_x, base.center_z, deg)
                self.assertAlmostEqual(foot.center_x, want[0], places=3)
                self.assertAlmostEqual(foot.center_z, want[1], places=3)
        odd = bg.xz_footprint(self.objs, 45.0, self.g)
        want = yaw_xz(base.center_x, base.center_z, 45.0)
        self.assertGreater(
            math.hypot(odd.center_x - want[0], odd.center_z - want[1]), 0.1
        )

    def test_a_prefab_with_no_measured_solid_is_named_not_guessed(self):
        path = blueprint(self.tmp, "unknown", [
            row("stone_floor", 0.0, 0.0, 0.0),
            row("modded_marble_floor_3x3", 8.0, 0.0, 0.0),
        ])
        foot = bg.xz_footprint(read_objects(path), 0.0, self.g)
        self.assertEqual(foot.pivot_only, ["modded_marble_floor_3x3"])
        # it contributed its pivot, so the box reaches exactly 8.0 and no further
        self.assertAlmostEqual(foot.max_x, 8.0, places=4)

    def test_an_empty_body_has_no_centre(self):
        with self.assertRaises(ValueError):
            bg.xz_footprint([], 0.0, self.g)


class CentringUnderRotation(unittest.TestCase):
    """The emitter end to end: a body must land centred on `--at` at every yaw.

    This is the acceptance test for the live `iron-era-workshop` placement.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.path = lopsided(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _plan(self, align: str, rotate: float, *extra: str) -> PlanBox:
        out = self.tmp / f"plan_{align}_{rotate:g}.txt"
        proc = subprocess.run(
            [sys.executable, str(BLUEPRINTS / "to_rcon_plan.py"), str(self.path),
             "--at", str(AT_X), str(PAD), str(AT_Z),
             "--align", align, "--rotate", str(rotate),
             "--allow-missing-prefabs", "--out", str(out), *extra],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        box = PlanBox(out)
        self.assertEqual(box.rows, len(LOPSIDED))
        return box

    def test_floor_center_lands_the_body_centre_on_the_pad_at_every_quarter_turn(self):
        for rotate in (0.0, 90.0, 180.0, 270.0):
            with self.subTest(rotate=rotate):
                box = self._plan("floor-center", rotate)
                cx, cz = box.centre
                self.assertAlmostEqual(cx, AT_X, delta=TOL_M)
                self.assertAlmostEqual(cz, AT_Z, delta=TOL_M)

    def test_the_centring_error_does_not_flip_sign_with_the_rotation(self):
        # The signature of the defect: centring before the yaw put the real
        # centre at `--at + R(yaw) * d`, so yaw 0 and yaw 180 were wrong by
        # equal and OPPOSITE amounts -- 1.51 m apart on this fixture. Comparing
        # the two directly catches that even if both were biased the same way.
        c0 = self._plan("floor-center", 0.0).centre
        c180 = self._plan("floor-center", 180.0).centre
        self.assertAlmostEqual(c0[0], c180[0], delta=TOL_M)
        self.assertAlmostEqual(c0[1], c180[1], delta=TOL_M)

    def test_the_body_spans_the_pad_symmetrically(self):
        # What the operator sees standing on the pad: equal overhang on both
        # sides. Asserting the extent as well as the centre means a footprint
        # that is centred but the wrong SIZE -- a pivot box, say -- still fails.
        box = self._plan("floor-center", 180.0)
        self.assertAlmostEqual(AT_X - box.min_x, box.max_x - AT_X, delta=TOL_M)
        self.assertAlmostEqual(AT_Z - box.min_z, box.max_z - AT_Z, delta=TOL_M)
        sx, sz = box.size
        self.assertAlmostEqual(sx, 22.4904, delta=TOL_M)
        self.assertAlmostEqual(sz, 22.4852, delta=TOL_M)

    def test_center_alone_centres_without_touching_y(self):
        # `center` runs no datum, so this isolates the XZ half of the change.
        for rotate in (0.0, 90.0, 180.0, 270.0):
            with self.subTest(rotate=rotate):
                box = self._plan("center", rotate)
                cx, cz = box.centre
                self.assertAlmostEqual(cx, AT_X, delta=TOL_M)
                self.assertAlmostEqual(cz, AT_Z, delta=TOL_M)
                self.assertAlmostEqual(box.pivot_y["stone_floor"], PAD, delta=1e-4)

    def test_floor_anchors_the_blueprint_origin_and_is_not_a_centring_mode(self):
        # `--align floor` is what was actually sent to Ulfsland, and it put the
        # blueprint's own origin -- a CORNER on this body and on
        # BjOrN_blueprint001 -- on the pad centre. That is its documented job;
        # this pins it so the two modes cannot be confused again by measurement.
        box = self._plan("floor", 180.0)
        cx, cz = box.centre
        want = yaw_xz(SOLID_CENTRE[0], SOLID_CENTRE[1], 180.0)
        self.assertAlmostEqual(cx, AT_X + want[0], delta=TOL_M)
        self.assertAlmostEqual(cz, AT_Z + want[1], delta=TOL_M)
        self.assertGreater(abs(cx - AT_X), 9.0)

    def test_the_floor_datum_still_lands_the_walkable_surface_on_the_pad(self):
        # The Y half of the placement was solved and verified in the live world;
        # this is the guard that the XZ rework did not move it. Both floor types
        # have their top at local +0.5, so both pivots land 0.5 below the pad.
        for rotate in (0.0, 90.0, 180.0, 270.0):
            with self.subTest(rotate=rotate):
                box = self._plan("floor-center", rotate)
                self.assertAlmostEqual(
                    box.pivot_y["stone_floor"], PAD - LEVEL_TOP + PIVOT_Y["stone_floor"],
                    delta=1e-3,
                )
                self.assertAlmostEqual(
                    box.pivot_y["wood_floor_1x1"],
                    PAD - LEVEL_TOP + PIVOT_Y["wood_floor_1x1"],
                    delta=1e-3,
                )


if __name__ == "__main__":
    unittest.main()
