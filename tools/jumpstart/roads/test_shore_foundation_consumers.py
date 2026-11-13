"""Measured support choices survive publication and real native regeneration."""
from pathlib import Path
import sys
import unittest

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import shores as S
import shore_build as SB
import north_transport as NT


def geometry(kind="jetty", **changes):
    spec = dict(id="foundation-consumer", node="dock-stathub", kind=kind,
                root=[0., 0.], bearing=0., length_m=24., width_tiles=2,
                width_m=4., inner_width_m=S.WF.VESSELS[S.VESSEL]["hull_w"] + 1.5)
    spec.update(changes)
    return spec


def coast():
    n = 257
    axis = np.arange(n, dtype=float) - n // 2
    z = np.broadcast_to(axis[:, None], (n, n))
    heights = np.where(z <= 0., S.DECK_Y,
                       np.maximum(20., S.DECK_Y - .8*z)).astype(np.float32)
    field = S.W.Field("foundation-coast", 0., 0., n/2, 1., n,
                      heights, np.ones((n, n), np.uint8))
    fields = {field.id: field}
    return fields, S.GeneratedSurface(fields)


def site_on_coast(kind):
    fields, surface = coast()
    spec = geometry(kind, length_m=32.)
    bed = S.A.Bed.applied(lambda x, z: surface.at(x, z)[0], "constructed measured coast")
    prefab, pieces, verdict = S.select_foundation(spec, bed)
    bx, bz = S._boat_pose(spec["root"], spec["bearing"], spec["length_m"],
                          kind, spec["width_m"])
    spec.update(pile_prefab=prefab, structural=verdict,
                boat=dict(prefab=S.VESSEL, x=bx, y=S.W.WATER_LEVEL, z=bz, yaw=0.))
    return spec, pieces, fields, surface


class ShoreFoundationConsumerTests(unittest.TestCase):
    def test_shallow_choice_preserves_original_timber_commands(self):
        spec = geometry()
        bed = S.A.Bed.applied(lambda x, z: 28., "ordinary shallow applied bed")
        prefab, pieces, verdict = S.select_foundation(spec, bed)
        original = S.emitted_pieces(S.A.jetty(spec["root"], spec["bearing"],
            spec["length_m"], S.DECK_Y, bed, width_tiles=2, name=spec["id"]))
        self.assertEqual(prefab, "wood_pole_log_4")
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual([S.A.command(p) for p in pieces],
                         [S.A.command(p) for p in original])

    def test_deep_choice_uses_stone_without_moving_deck_or_rails(self):
        spec = geometry(bearing=37.)
        bed = S.A.Bed.applied(lambda x, z: 20., "deep applied bed")
        timber = S.waterfront_pieces(dict(spec, pile_prefab="wood_pole_log_4"), bed)
        failed = S.A.verify(timber, bed, note="unchanged timber")
        self.assertFalse(failed["ok"], failed)
        self.assertTrue(any(f["min_support"] == 10. and f["needed"] == 20.
                            for f in failed["failures"]), failed)
        prefab, pieces, verdict = S.select_foundation(spec, bed)
        self.assertEqual(prefab, "stone_pillar")
        self.assertTrue(verdict["ok"], verdict)
        non_piles = lambda rows: [S.A.command(p) for p in rows if not p.role.endswith("/pile")]
        self.assertEqual(non_piles(pieces), non_piles(timber))
        self.assertTrue(all(p.yaw == 37. for p in pieces if p.prefab == "stone_pillar"))

    def test_both_designs_refuse_with_ordered_numeric_support_evidence(self):
        bed = S.A.Bed.applied(lambda x, z: 0., "too deep for either measured foundation")
        with self.assertRaises(S.FoundationRefusal) as caught:
            S.select_foundation(geometry(), bed)
        attempts = caught.exception.attempts
        self.assertEqual([a["pile_prefab"] for a in attempts], list(S.A.JETTY_PILE_PREFABS))
        for attempt in attempts:
            self.assertTrue(attempt["failures"], attempt)
            self.assertEqual(attempt["safety_factor"], 2.)
            self.assertTrue(all(f["support"] < f["needed"] for f in attempt["failures"]))

    def test_persisted_choice_is_binding_and_still_requires_actual_support(self):
        shallow = S.A.Bed.applied(lambda x, z: 28., "shallow applied bed")
        chosen, pieces, verdict = S.select_foundation(geometry(pile_prefab="stone_pillar"), shallow)
        self.assertEqual(chosen, "stone_pillar")
        self.assertTrue(verdict["ok"], verdict)
        self.assertTrue(any(p.prefab == "stone_pillar" for p in pieces))
        deep = S.A.Bed.applied(lambda x, z: 20., "deep applied bed")
        with self.assertRaises(S.FoundationRefusal) as caught:
            S.select_foundation(geometry(pile_prefab="wood_pole_log_4"), deep)
        self.assertEqual([a["pile_prefab"] for a in caught.exception.attempts], ["wood_pole_log_4"])

    def test_yaml_selected_jetty_and_slip_emit_same_verified_stone_design(self):
        for kind in ("jetty", "slip"):
            with self.subTest(kind=kind):
                site, pieces, fields, surface = site_on_coast(kind)
                self.assertEqual(site["pile_prefab"], "stone_pillar")
                restored = yaml.safe_load(yaml.safe_dump(site))
                model = SB.plan_site(restored, surface)
                self.assertEqual(model["pile_prefab"], "stone_pillar")
                self.assertTrue(model["boat_clearance"]["ok"], model["boat_clearance"])
                dock = model["ops"][0]
                self.assertEqual(dock["meta"]["pile_prefab"], "stone_pillar")
                self.assertTrue(dock["meta"]["structural_verdict"]["ok"])
                self.assertEqual(dock["wire"], [S.A.command(p) for p in pieces])
                self.assertEqual(model["pieces"], NT._piece_rows(dock["wire"]))
                self.assertTrue(any(p["prefab"] == "stone_pillar" for p in model["pieces"]))
                self.assertFalse(any(op["op"] == "terrain_write" for op in model["ops"]))

    def test_supported_stone_cannot_accept_a_boat_inside_its_pier(self):
        site, pieces, fields, surface = site_on_coast("jetty")
        site["boat"].update(x=0., z=16.)
        with self.assertRaisesRegex(SystemExit, "obstructs its measured hull"):
            SB.plan_site(site, surface)

    def test_north_terminal_rebuild_honors_yaml_selected_foundation(self):
        fields, surface = coast()
        seed, world = S.worldseed.seed(), S.worldseed.world()
        terminals = {
            "north-dock-test": dict(xz=[0., 0.], sea_xz=[0., 24.],
                segment_id="test-road", final_departure=False, vessel=S.VESSEL),
            "north-dock-final": dict(xz=[60., 0.], sea_xz=[60., 24.],
                segment_id="final-road", final_departure=True),
        }
        itinerary = dict(schema_version=1, seed=seed, world=world, provisional=False,
            nodes={"a": {"xz": [0., -8.]}, "b": {"xz": [0., 0.]},
                   "c": {"xz": [60., -8.]}, "d": {"xz": [60., 0.]}},
            road_edges=[dict(id="test-road", a="a", b="b"),
                        dict(id="final-road", a="c", b="d")],
            terminals=terminals, final_departure="north-dock-final",
            transport=[dict(id="test-passage", a="north-dock-test", b="north-dock-final",
                            sea_route=[[0., 24.], [60., 24.]])])
        scoped = NT.ScopedFields(fields, seed, source="constructed measured coast")
        model = NT.plan_terminal(itinerary, "north-dock-test", scoped, surface=surface)
        self.assertEqual(model["pile_prefab"], "stone_pillar")
        terminals["north-dock-test"]["pile_prefab"] = model["pile_prefab"]
        restored = yaml.safe_load(yaml.safe_dump(itinerary))
        rebuilt = NT.plan_terminal(restored, "north-dock-test", scoped, surface=surface)
        self.assertEqual(rebuilt["ops"][0]["wire"], model["ops"][0]["wire"])
        self.assertEqual(rebuilt["pieces"], model["pieces"])
        self.assertEqual(rebuilt["ops"][0]["meta"]["pile_prefab"], "stone_pillar")
        self.assertTrue(rebuilt["boat_clearance"]["ok"], rebuilt["boat_clearance"])
        self.assertFalse(any(op["op"] == "terrain_write" for op in rebuilt["ops"]))


if __name__ == "__main__":
    unittest.main()
