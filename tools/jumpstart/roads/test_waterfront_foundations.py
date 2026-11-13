"""Measured waterfront foundations retain deck geometry and real support guards."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "crossings"))
import assemble as A


def flat_bed(height):
    return A.Bed.applied(lambda x, z: height, f"flat applied bed at {height} m")


def emitted(pieces):
    """Exercise seating/support on millimetre-rounded emitted coordinates too."""
    return [replace(p, x=float(A.fmt(p.x)), y=float(A.fmt(p.y)),
                    z=float(A.fmt(p.z)), yaw=float(A.fmt(p.yaw))) for p in pieces]


def geometry(pieces):
    return [(p.prefab, p.x, p.y, p.z, p.yaw, p.role, p.lateral, p.overhang)
            for p in pieces]


class WaterfrontFoundationTests(unittest.TestCase):
    def assert_seated(self, pieces, bed):
        report = A.check_seating(pieces, bed)
        for key in ("unfounded_columns", "piles_under_the_bed",
                    "structure_under_the_bed", "pieces_unjudged",
                    "travelled_way_buried"):
            self.assertEqual(report[key], [], (key, report))
        A.refuse_on_colocated(pieces, "foundation test")
        return report

    def test_measured_order_and_ordinary_default_timber_survives(self):
        bed = flat_bed(28.0)
        args = ((0.0, 0.0), 37.0, 8.0, A.DECK_Y, bed)
        default = A.jetty(*args)
        piles = [p for p in default if p.role.endswith("/pile")]
        for p in piles:
            self.assertEqual(p.prefab, "wood_pole_log_4")
            self.assertEqual(p.yaw, 0.0)
        rounded = emitted(default)
        self.assert_seated(rounded, bed)
        proof = A.verify(rounded, bed)
        self.assertTrue(proof["ok"], proof)
        self.assertEqual(proof["safety_factor"], 2.0)

    def test_deep_timber_refuses_but_stone_supports_identical_deck_and_rails(self):
        bed = flat_bed(20.0)
        choices = {}
        for prefab in A.JETTY_PILE_PREFABS:
            pieces = A.jetty((0.0, 0.0), 0.0, 12.0, A.DECK_Y, bed,
                             pile_prefab=prefab)
            pieces = emitted(pieces)
            self.assert_seated(pieces, bed)
            choices[prefab] = pieces
        timber, stone = (choices[prefab] for prefab in A.JETTY_PILE_PREFABS)
        self.assertEqual(
            geometry([p for p in timber if not p.role.endswith("/pile")]),
            geometry([p for p in stone if not p.role.endswith("/pile")]))
        wood_proof = A.verify(timber, bed)
        stone_proof = A.verify(stone, bed)
        self.assertFalse(wood_proof["ok"], wood_proof)
        self.assertTrue(any(f["needed"] == 20.0 and f["support"] < 20.0
                            for f in wood_proof["failures"]), wood_proof)
        self.assertTrue(stone_proof["ok"], stone_proof)
        for proof in (wood_proof, stone_proof):
            self.assertEqual(proof["safety_factor"], 2.0)
            self.assertEqual(proof["no_in_water_violations"], [])
        stone_piles = [p for p in stone if p.role.endswith("/pile")]
        self.assertTrue(any(p.on_terrain for p in stone_piles))
        for piece in stone_piles:
            material = A.facts()[piece.prefab].material
            self.assertGreaterEqual(piece.support, 2. * A.MATERIAL_PROPS[material][1])

    def test_stone_base_with_timber_cap_keeps_each_material_safety_margin(self):
        bed = flat_bed(17.5)
        pieces = emitted(A.jetty((0., 0.), 335., 72., A.DECK_Y, bed,
                                 width_tiles=3, pile_prefab="stone_pillar"))
        self.assert_seated(pieces, bed)
        verdict = A.verify(pieces, bed)
        self.assertTrue(verdict["ok"], verdict)
        self.assertTrue(any(p.prefab == "stone_pillar" for p in pieces))
        self.assertTrue(any(p.prefab == "wood_pole_log" for p in pieces))
        for piece in pieces:
            fact = A.facts()[piece.prefab]
            if fact.supports:
                self.assertGreaterEqual(piece.support, 2. * A.MATERIAL_PROPS[fact.material][1])

    def test_stone_orientation_tracks_deck_and_keeps_its_local_footprint(self):
        bed = flat_bed(28.0)
        for bearing in (0.0, 37.0, 90.0, 213.0):
            with self.subTest(bearing=bearing):
                pieces = emitted(A.jetty((3.25, -7.25), bearing, 8.0,
                                         A.DECK_Y, bed,
                                         pile_prefab="stone_pillar"))
                self.assert_seated(pieces, bed)
                piles = [p for p in pieces if p.role.endswith("/pile")]
                self.assertTrue(piles)
                ux, uz = A._unit(bearing)
                lx, lz = uz, -ux
                for p in piles:
                    self.assertEqual(p.prefab, "stone_pillar")
                    self.assertEqual(p.yaw, bearing)
                    dx, dz = p.x - 3.25, p.z + 7.25
                    lateral = dx * lx + dz * lz
                    station = dx * ux + dz * uz
                    self.assertAlmostEqual(abs(lateral), 1.0, delta=0.001)
                    self.assertLess(min(abs(station - t) for t in (4.0, 8.0)),
                                    0.001)
                    # The measured pillar's local half-width is inside the
                    # measured floor tile; matching yaw keeps that true obliquely.
                    pillar, floor = A.facts()[p.prefab], A.facts()["wood_floor"]
                    for axis in (0, 2):
                        self.assertGreaterEqual(pillar.lo[axis], floor.lo[axis])
                        self.assertLessEqual(pillar.hi[axis], floor.hi[axis])
                proof = A.verify(pieces, bed)
                self.assertTrue(proof["ok"], proof)

    def test_measured_stack_pitch_extra_embed_and_real_terrain_contact(self):
        bed = A.Bed.applied(lambda x, z: 21.2 + 0.2 * x,
                            "applied sloped bed, distinct pivot and lattice")
        column = emitted(A._pile_stack(0.25, -0.25, A.DECK_Y, bed, "test",
                                       pile_prefab="stone_pillar", yaw=37.0))
        measured = A.facts()["stone_pillar"]
        height = measured.hi[1] - measured.lo[1]
        lowest = min(column, key=lambda p: p.y)
        regular = sorted((p for p in column if p is not lowest),
                         key=lambda p: p.y, reverse=True)
        for upper, lower in zip(regular, regular[1:]):
            self.assertAlmostEqual(upper.y - lower.y, height)
        self.assertAlmostEqual(column[0].y + measured.hi[1], A.DECK_Y - 0.13)
        self.assertAlmostEqual(lowest.y + measured.hi[1], A.bed_under(lowest, bed))
        self.assertGreaterEqual(bed(lowest.x, lowest.z) - lowest.aabb()[0][1], 1.0)
        for upper, lower in zip(column, column[1:]):
            self.assertLessEqual(upper.aabb()[0][1], lower.aabb()[1][1] + 0.001)
        self.assert_seated(column, bed)
        proof = A.verify(column, bed)
        self.assertTrue(proof["ok"], proof)
        self.assertTrue(lowest.on_terrain)
        self.assertEqual(lowest.support, A.MATERIAL_PROPS[measured.material][0])
        floating = [replace(p, y=p.y + 40.0) for p in column]
        floating_proof = A.verify(floating, bed)
        self.assertFalse(floating_proof["ok"], floating_proof)
        self.assertEqual(floating_proof["terrain_anchored"], 0)
        with self.assertRaises(SystemExit):
            A.refuse_on_seating(floating, bed, "floating stone")

    def test_slip_propagates_choice_to_both_fingers_without_moving_head(self):
        # A real bank anchors the closed head; the fingers still stand over
        # deep water. No extra head piles or terrain edits are introduced.
        bed = A.Bed.applied(lambda x, z: 30.55 if z <= 1.1 else 20.0,
                            "applied bank at closed head, deep berth seaward")
        args = ((0.0, 0.0), 0.0, 12.0, 11.0, A.DECK_Y, bed)
        timber = A.slip(*args)
        stone = emitted(A.slip(*args, pile_prefab="stone_pillar"))
        self.assertEqual(
            geometry([p for p in emitted(timber) if not p.role.endswith("/pile")]),
            geometry([p for p in stone if not p.role.endswith("/pile")]))
        for finger in ("fingerL", "fingerR"):
            piles = [p for p in stone if f"/{finger}/" in p.role
                     and p.role.endswith("/pile")]
            self.assertTrue(piles)
            self.assertTrue(any(p.prefab == "stone_pillar" for p in piles))
        self.assertTrue(any(p.role.endswith("/head") for p in stone))
        self.assert_seated(stone, bed)
        proof = A.verify(stone, bed)
        self.assertTrue(proof["ok"], proof)

    def test_both_foundations_refuse_an_unreachable_depth_honestly(self):
        bed = flat_bed(0.0)
        for prefab in A.JETTY_PILE_PREFABS:
            with self.subTest(prefab=prefab):
                pieces = emitted(A.jetty((0.0, 0.0), 0.0, 8.0,
                                         A.DECK_Y, bed, pile_prefab=prefab))
                self.assert_seated(pieces, bed)
                proof = A.verify(pieces, bed)
                self.assertFalse(proof["ok"], proof)
                self.assertTrue(proof["failures"])
                self.assertTrue(all(f["support"] < f["needed"]
                                    for f in proof["failures"]))

    def test_non_foundation_and_unmeasured_names_are_refused(self):
        bed = flat_bed(28.0)
        for prefab in ("wood_floor", "missing_unmeasured_pillar", "iron_pole"):
            with self.subTest(prefab=prefab):
                with self.assertRaisesRegex(ValueError, "pile prefab"):
                    A.jetty((0.0, 0.0), 0.0, 8.0, A.DECK_Y, bed,
                            pile_prefab=prefab)
                with self.assertRaisesRegex(ValueError, "pile prefab"):
                    A.slip((0.0, 0.0), 0.0, 8.0, 11.0, A.DECK_Y, bed,
                           pile_prefab=prefab)
        for prefab in A.JETTY_PILE_PREFABS:
            with self.subTest(prefab=prefab):
                with self.assertRaises(SystemExit):
                    A.jetty((0.0, 0.0), 0.0, 8.0, A.DECK_Y,
                            A.Bed.generated(lambda x, z: 28.0, "unapplied bed"),
                            pile_prefab=prefab)


if __name__ == "__main__":
    unittest.main()
