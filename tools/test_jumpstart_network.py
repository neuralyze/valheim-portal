#!/usr/bin/env python3
"""Tests for the world-network router, bridge selection and portal arithmetic.

Every case here pins a contract that a plausible bug would break, and the bugs are
not hypothetical -- two of them had already happened in this tree:

  * THE WATER DATUM. Valheim's water plane is y = 30 in WorldGenerator.GetHeight
    units, not 0. A solver that used 0.0 put 10 of 13 placements under the sea. If
    the router regresses to a 0 datum it sees no water anywhere, emits no bridge,
    and happily lays a carriageway along a river bed -- so `test_crosses_water_with_a_bridge`
    fails the moment the datum is wrong.

  * THE SHORT BRIDGE. A 12 m bridge over a 40 m river is a bug, and the naive
    implementation -- take the first blueprint in the list -- produces exactly that.
    `test_oversize_span_is_a_blocker_not_a_short_bridge` asserts the router refuses
    rather than emitting a deck that does not reach.

The corridors here are synthetic, so these run in milliseconds and need no game
install. The real seed is exercised by `cli.py route`, which boots a sandbox copy of
the dedicated server; that is a measurement, not a unit test.
"""

import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent / "jumpstart"
sys.path.insert(0, str(ROOT))

from network import catalogue, plan, portals, router, sites  # noqa: E402
from network.terrain import WATER_LEVEL, Corridor  # noqa: E402

POLICY = portals.load_policy()


def flat(width: int, depth: int, height: float, biome: int = 1) -> Corridor:
    h = np.full((depth, width), height, dtype=np.float32)
    b = np.full((depth, width), biome, dtype=np.uint8)
    return Corridor(x0=0, z0=0, height=h, biome=b, seed="synthetic")


def channel(cor: Corridor, x0: int, x1: int, bed: float, z0: int = 0, z1: int | None = None) -> Corridor:
    """Carve a north-south water channel between x0 and x1 (inclusive)."""
    z1 = cor.height.shape[0] if z1 is None else z1
    cor.height[z0:z1, x0 : x1 + 1] = bed
    return cor


class WaterAndBridges(unittest.TestCase):
    def test_water_datum_is_thirty_not_zero(self):
        # Not a tautology: it asserts the router reads the datum from the one
        # definition in the tree rather than carrying its own copy.
        self.assertEqual(WATER_LEVEL, 30.0)
        self.assertEqual(float(POLICY["water"]["level_m"]), WATER_LEVEL)

    def test_crosses_water_with_a_bridge(self):
        # Terrain at 40 m is dry; a channel with its bed at 26 m is 4 m under the
        # water plane. With a 0 datum the channel reads as dry land 26 m high and no
        # bridge is emitted at all.
        cor = channel(flat(200, 80, 40.0), 96, 107, 26.0)
        r = router.route(cor, (20, 40), (180, 40), POLICY, a_name="west", b_name="east")
        self.assertEqual(len(r.crossings), 1, "a 12 m channel must produce exactly one crossing")
        c = r.crossings[0]
        self.assertAlmostEqual(c.span_m, 14.0, delta=4.0)
        self.assertGreater(c.max_depth_m, 3.9)
        self.assertEqual(c.blueprint, "speeds-bridge2.blueprint")
        self.assertEqual(c.spans, 1)
        self.assertIsNone(c.problem)
        self.assertEqual(r.audit(), [])

    def test_no_land_node_sits_below_the_road_freeboard(self):
        cor = channel(flat(200, 80, 40.0), 96, 107, 26.0)
        r = router.route(cor, (20, 40), (180, 40), POLICY)
        floor = WATER_LEVEL + float(POLICY["water"]["road_freeboard_m"])
        for n in r.nodes:
            if not n.water:
                self.assertGreaterEqual(n.y, floor, f"land node ({n.x},{n.z}) at {n.y}")

    def test_router_detours_to_the_narrows(self):
        # A 60 m channel everywhere except a 10 m neck 300 m off the direct line.
        # A uniform per-metre water cost takes the straight line and needs a 60 m
        # bridge; the abutment-plus-span model walks to the neck and needs 14 m. This
        # is the single design decision the whole router turns on.
        cor = flat(400, 500, 40.0)
        channel(cor, 170, 229, 24.0)
        cor.height[300:308, 180:230] = 40.0  # fill all but 10 m of the channel here
        r = router.route(cor, (60, 60), (340, 60), POLICY)
        self.assertEqual(len(r.crossings), 1)
        c = r.crossings[0]
        self.assertLess(c.span_m, 22.0, f"crossed at span {c.span_m}, i.e. not at the neck")
        self.assertGreater(r.length_m, 500.0, "reaching the neck has to cost real distance")
        self.assertGreater(r.detour_ratio(), 1.8)
        self.assertEqual(c.blueprint, "speeds-bridge2.blueprint")
        self.assertEqual(r.audit(), [])

    def test_oversize_span_is_a_blocker_not_a_short_bridge(self):
        # 150 m of water, 20 m deep: wider than long-bridge's verified 89.3 m span and
        # deeper than the 12 m pier limit, so there is no honest answer and the router
        # must say so instead of stamping a 26 m deck across it.
        cor = channel(flat(400, 80, 40.0), 120, 269, 10.0)
        r = router.route(cor, (20, 40), (380, 40), POLICY)
        self.assertEqual(len(r.crossings), 1)
        c = r.crossings[0]
        self.assertIsNotNone(c.problem, "an unbridgeable span must be reported")
        self.assertIn("pier limit", c.problem)
        self.assertIsNone(c.blueprint)
        self.assertTrue(r.audit(), "audit must fail a route with an unbridgeable crossing")
        p = plan.build(r, POLICY)
        self.assertTrue(p.blockers)

    def test_single_span_is_upsized_before_it_is_piered(self):
        # 60 m at 4 m deep. 60 + 4 m of abutment fits inside long-bridge's verified
        # 89.3 m, so the answer is ONE oversized span, not five piered ones:
        # overshooting a span is free, undershooting it is a hole.
        cor = channel(flat(300, 80, 40.0), 120, 179, 26.0)
        r = router.route(cor, (20, 40), (280, 40), POLICY)
        c = r.crossings[0]
        self.assertIsNone(c.problem)
        self.assertEqual(c.blueprint, "long-bridge.blueprint")
        self.assertEqual(c.spans, 1)
        self.assertEqual(c.pier_positions, [])
        self.assertEqual(r.audit(), [])

    def test_piered_multispan_reaches_the_far_bank(self):
        # 150 m of water only 4 m deep: wider than every verified span, shallow enough
        # to pier. The reach check is the point -- spans * span_m must cover the gap.
        cor = channel(flat(400, 80, 40.0), 120, 269, 26.0)
        r = router.route(cor, (20, 40), (380, 40), POLICY)
        c = r.crossings[0]
        self.assertIsNone(c.problem)
        self.assertGreater(c.spans, 1)
        self.assertGreaterEqual(c.blueprint_span_m * c.spans, c.span_m + 4.0)
        self.assertEqual(len(c.pier_positions), c.spans - 1)
        self.assertEqual(r.audit(), [])

    def test_only_verified_span_blueprints_are_auto_selected(self):
        # bridge.vbuild and salty-dick-bridge-curved-final have near-square bounding
        # boxes, so their deck orientation is unknown; god-ponte-plus has discarded
        # rows. None of them may be chosen automatically.
        auto = {b["name"] for b in POLICY["bridge"]["blueprints"] if b.get("verified_span")}
        self.assertEqual(auto, {"speeds-bridge2.blueprint", "long-bridge.blueprint"})
        for b in POLICY["bridge"]["blueprints"]:
            if b.get("verified_span"):
                self.assertEqual(b["verdict"], "PLACES_CLEAN")
                self.assertEqual(b["discarded_rows"], 0)


class GradeAndAvoidance(unittest.TestCase):
    def _ramp(self, rise_per_m: float, width: int = 200, depth: int = 400) -> Corridor:
        """A hillside rising along +x, with `depth` metres of room across it."""
        cor = flat(width, depth, 40.0)
        for j in range(width):
            cor.height[:, j] = 40.0 + j * rise_per_m
        return cor

    def test_a_cliff_is_refused_rather_than_climbed(self):
        # A 50 m vertical step. No heading makes this legal, so there is no route --
        # unlike a steep SLOPE, which a road climbs by switchbacking (below).
        cor = flat(120, 60, 40.0)
        cor.height[:, 60:] = 90.0
        with self.assertRaises(router.RouteError):
            router.route(cor, (10, 30), (110, 30), POLICY)

    def test_no_step_exceeds_the_impassable_limit(self):
        r = router.route(self._ramp(0.2), (10, 30), (190, 30), POLICY)
        hard = float(POLICY["grade"]["impassable"])
        for p, q in zip(r.nodes, r.nodes[1:]):
            run = math.hypot(q.x - p.x, q.z - p.z)
            self.assertLessEqual(abs(q.y - p.y) / run, hard + 1e-9)

    def test_a_steep_slope_is_switchbacked_not_bulldozed(self):
        # The point of earthworks_weight: given room, the router zigzags across a
        # 0.5 m/m hillside to hold the 0.30 budget instead of driving straight up it.
        # A router without the penalty goes straight and reports 0.5 everywhere.
        wide = self._ramp(0.5)
        r = router.route(wide, (10, 200), (190, 200), POLICY)
        self.assertLessEqual(r.max_grade, float(POLICY["grade"]["max_road"]) + 1e-6)
        self.assertEqual(r.over_budget_m, 0.0)
        self.assertGreater(r.detour_ratio(), 1.4, "holding the grade has to cost distance")

    def test_earthworks_are_reported_when_there_is_no_room_to_switchback(self):
        # Same hillside, but a 6 m wide defile: switchbacks do not fit, so the road
        # must go over budget AND say how much cut and fill that implies rather than
        # reporting a grade it is not achieving.
        # 4 m across: with a 2 m lattice the only headings left are straight and one
        # 2 m sidestep, and the flattest of those is 0.354 m/m -- over the 0.30 budget.
        cor = self._ramp(0.5, depth=4)
        r = router.route(cor, (10, 0), (190, 0), POLICY)
        self.assertGreater(r.over_budget_m, 0.0)
        self.assertGreater(r.cut_fill_m, 0.0)
        self.assertGreater(r.max_grade, float(POLICY["grade"]["max_road"]))

    def test_hard_class_location_is_never_crossed(self):
        # Haldor sits on the straight line with a 60 m hard keep-out.
        cor = flat(300, 200, 40.0)
        loc = [{"name": "Vendor_BlackForest", "x": 150.0, "z": 100.0}]
        r = router.route(cor, (20, 100), (280, 100), POLICY, loc)
        for n in r.nodes:
            self.assertGreater(
                math.hypot(n.x - 150.0, n.z - 100.0), 59.0,
                f"node ({n.x},{n.z}) is inside Haldor's keep-out",
            )

    def test_soft_class_location_is_costly_but_passable(self):
        # A ruin must not block a road. Without the soft/hard split every one of
        # Pirate68's 12,301 location instances becomes a wall.
        cor = flat(300, 200, 40.0)
        loc = [{"name": "Ruin1", "x": 150.0, "z": 100.0}]
        r = router.route(cor, (20, 100), (280, 100), POLICY, loc)
        self.assertGreater(r.length_m, 0)
        self.assertEqual(r.audit(), [])


class Costing(unittest.TestCase):
    def test_terrain_costs_no_zdos_and_paving_costs_one_per_metre(self):
        # The measurement this encodes: 0 TerrainModifier / piece_pavedroad objects in
        # 111,784 persistent objects across three real worlds. If someone recosts hoe
        # work as a piece, the recommendation inverts and this fails.
        cor = flat(600, 60, 40.0)
        r = router.route(cor, (10, 30), (590, 30), POLICY)
        p = plan.build(r, POLICY)
        by = p.by_channel()
        self.assertGreater(by["terrain"]["items"], 100)
        self.assertEqual(by["terrain"]["zdo"], 0)
        alt = plan.paved_alternative(r, POLICY)
        self.assertGreater(alt["zdo_cost"], 4 * p.zdo_cost())

    def test_blueprint_stamps_cost_their_piece_count(self):
        # A bridge is 63 pieces, not 1. Costing a stamp as one object understates a
        # road with four bridges by two hundred ZDOs.
        self.assertEqual(plan.blueprint_pieces("speeds-bridge2.blueprint"), 63)
        self.assertEqual(plan.blueprint_pieces("PiNoKi_SmallHut.blueprint"), 48)
        cor = channel(flat(200, 80, 40.0), 96, 107, 26.0)
        r = router.route(cor, (20, 40), (180, 40), POLICY)
        p = plan.build(r, POLICY)
        self.assertEqual(p.by_channel()["blueprint"]["zdo"], 63)

    def test_rcon_lines_cover_pieces_only(self):
        cor = channel(flat(200, 80, 40.0), 96, 107, 26.0)
        p = plan.build(router.route(cor, (20, 40), (180, 40), POLICY), POLICY)
        lines = p.rcon_lines()
        self.assertTrue(lines)
        self.assertTrue(all(l.startswith("spawn ") and "-rotation" in l for l in lines))
        # Terrain and blueprint channels are not RCON-placeable, so they must not leak
        # into the spawn plan: piece_pavedroad leaves no ZDO and a blueprint is not a
        # prefab.
        self.assertNotIn("piece_pavedroad", " ".join(lines))
        self.assertNotIn(".blueprint", " ".join(lines))


class Doorstep(unittest.TestCase):
    def test_doorstep_is_integers_with_freeboard_and_no_spawnable_yaw(self):
        cor = flat(300, 300, 40.0)
        site = sites.solve(cor, dict(id="home", biome="Meadows", pad_m=20, min_freeboard_m=3.0), None)
        ds = sites.doorstep(cor, site, (site.x + 100, site.z), preset="pre-eikthyr")
        for k in ("x", "y", "z"):
            self.assertIsInstance(ds[k], int, f"{k} must be an integer at the point of truth")
        self.assertGreaterEqual(ds["freeboard_m"], 2.0)
        self.assertEqual(ds["y"] - 30, round(ds["freeboard_m"]))
        # PRESET-QUALIFIED: `early-dock` is a placement id in two presets that solve
        # to different sites, so a bare id is ambiguous across the tree.
        self.assertEqual(ds["for_placement"], "pre-eikthyr#home")
        self.assertEqual(ds["installation"], "home")
        self.assertEqual(ds["preset"], "pre-eikthyr")
        self.assertIn("NOT consumed", ds["status"])
        self.assertTrue(ds["approach"]["ok"])
        self.assertTrue(ds["approach"]["dry_to_footprint_edge"])
        # ServerCharacters' spawn block is {x, y, z} integers and an unknown key is
        # fatal to the whole template, so facing must be a separate, clearly-named,
        # non-spawn field.
        self.assertNotIn("yaw", ds)
        self.assertIn("approach_yaw_deg", ds)
        self.assertIn("router-only", ds["approach_yaw_note"])

    def test_doorstep_clears_the_footprint_stand_off(self):
        cor = flat(300, 300, 40.0)
        site = sites.solve(cor, dict(id="home", biome="Meadows", pad_m=20, min_freeboard_m=3.0), None)
        ds = sites.doorstep(cor, site, (site.x + 100, site.z), clear_m=6.0)
        self.assertGreaterEqual(math.hypot(ds["x"] - site.x, ds["z"] - site.z), 6.0)

    def test_thresholds_come_from_derive_not_from_literals(self):
        # The drift risk: a second implementation of the walk is fine, duplicated
        # THRESHOLDS are not. Raise derive.py's bar and a previously-passing doorstep
        # must start being refused -- if the numbers were literals in sites.py it
        # would keep passing at the old bar and disagree with derive's verdict.
        self.assertEqual(sites.DERIVE.SEA_LEVEL_M, WATER_LEVEL)
        cor = flat(300, 300, 40.0)
        site = sites.solve(cor, dict(id="home", biome="Meadows", pad_m=20, min_freeboard_m=3.0), None)
        self.assertTrue(sites.doorstep(cor, site, (site.x + 100, site.z))["approach"]["ok"])
        old = sites.DERIVE.SPAWN_FREEBOARD_M
        try:
            sites.DERIVE.SPAWN_FREEBOARD_M = 100.0  # ground is only 10 m clear
            with self.assertRaises(ValueError):
                sites.doorstep(cor, site, (site.x + 100, site.z))
        finally:
            sites.DERIVE.SPAWN_FREEBOARD_M = old
        old = sites.DERIVE.SPAWN_PATH_STEP_M
        try:
            sites.DERIVE.SPAWN_PATH_STEP_M = -1.0  # any step at all now fails the walk
            cor.height[site.z, site.x + 8] = 41.0
            with self.assertRaises(ValueError):
                sites.doorstep(cor, site, (site.x + 100, site.z), footprint_xz_m=(8.0, 8.0), max_m=30.0)
        finally:
            sites.DERIVE.SPAWN_PATH_STEP_M = old

    def test_doorstep_refuses_when_a_ditch_separates_it_from_the_building(self):
        # The one part of the verdict a dry route cannot imply: the last few metres
        # from the doorstep to the building's near edge. A dry doorstep on dry ground
        # is still wrong if there is water between it and the door.
        cor = flat(300, 300, 40.0)
        site = sites.solve(cor, dict(id="home", biome="Meadows", pad_m=20, min_freeboard_m=3.0), None)
        ok = sites.doorstep(cor, site, (site.x + 100, site.z), footprint_xz_m=(8.0, 8.0))
        self.assertTrue(ok["approach"]["ok"])
        # Flood a 3 m ditch just outside the footprint edge, on the approach bearing.
        cor.height[site.z - 8 : site.z + 9, site.x + 5 : site.x + 9] = WATER_LEVEL - 2.0
        with self.assertRaises(ValueError) as caught:
            sites.doorstep(cor, site, (site.x + 100, site.z), footprint_xz_m=(8.0, 8.0), max_m=30.0)
        self.assertIn("dry walk to the footprint edge", str(caught.exception))

    def test_doorstep_refuses_rather_than_placing_a_player_in_water(self):
        cor = flat(300, 300, 40.0)
        site = sites.solve(cor, dict(id="home", biome="Meadows", pad_m=20, min_freeboard_m=3.0), None)
        cor.height[:, :] = WATER_LEVEL - 5.0          # flood everything...
        cor.height[site.z - 3 : site.z + 4, site.x - 3 : site.x + 4] = 40.0  # ...but the pad
        with self.assertRaises(ValueError):
            sites.doorstep(cor, site, (site.x + 100, site.z))


class Model(unittest.TestCase):
    def setUp(self):
        self.cat = catalogue.load()

    def test_catalogue_is_coherent(self):
        self.assertEqual(self.cat.validate(), [])

    def test_every_blueprint_reference_resolves_to_a_manifest_sha256(self):
        seen = 0
        for inst in self.cat.installations:
            for bp in (inst.blueprint, inst.companion_blueprint):
                if bp is None:
                    continue
                seen += 1
                self.assertEqual(len(bp.sha256), 64, f"{inst.id}: {bp.name}")
                # This repo is published and the corpus came from valheimians.com,
                # whose terms forbid redistribution. A body may only be committed
                # when its own licence permits it -- MEASURED: exactly 5 of the 176
                # manifest rows are committed and all 5 carry MIT or Unlicense.
                if bp.body == "committed":
                    self.assertIn(
                        bp.licence, {"MIT", "Unlicense (public domain)", "AGPL-3.0"},
                        f"{inst.id}: {bp.name} has a committed body under '{bp.licence}'",
                    )
                else:
                    self.assertEqual(bp.body, "reference", f"{inst.id}: {bp.name}")
        self.assertGreater(seen, 15)

    def test_the_set_accumulates_across_tiers(self):
        prev: set[str] = set()
        for tier in sorted(self.cat.tiers):
            ids = {i.id for i in self.cat.set_for_tier(tier)}
            self.assertTrue(prev <= ids, f"tier {tier} dropped {prev - ids}")
            prev = ids
        self.assertGreater(len(prev), len({i.id for i in self.cat.set_for_tier(1)}))

    def test_every_tier_has_exactly_one_new_home_base_and_spawns_there(self):
        for tier, preset in sorted(self.cat.tiers.items()):
            new = [i for i in self.cat.set_for_tier(tier) if i.role == "home_base" and i.first_tier == tier]
            self.assertEqual(len(new), 1, f"tier {tier} ({preset})")
            self.assertTrue(new[0].is_spawn_home)
            self.assertIs(self.cat.home_base_for_tier(tier), new[0])

    def test_no_crop_is_planted_outside_its_biome(self):
        # EnforceBiomesVanilla is true in the live PlantEverything config (MEASURED),
        # so a Plains-only crop in a Meadows plot simply never grows.
        for inst in self.cat.installations:
            for crop in inst.crops:
                allowed = self.cat.crop_biomes.get(inst.biome, [])
                if allowed:
                    self.assertIn(crop, allowed, f"{inst.id} in {inst.biome}")
        barley_biomes = [b for b, cs in self.cat.crop_biomes.items() if "sapling_barley" in cs]
        self.assertEqual(barley_biomes, ["Plains"])


class PortalGraph(unittest.TestCase):
    def setUp(self):
        self.cat = catalogue.load()

    def test_portal_pieces_equal_two_per_edge(self):
        # The whole honesty of the topology: an edge is two portal pieces sharing a
        # tag, so a hub with N spokes needs N pieces. A model that costs one piece per
        # installation understates the hub by N-1.
        for tier in sorted(self.cat.tiers):
            g = portals.build(self.cat, tier)
            self.assertEqual(g.piece_total(), 2 * len(g.edges), f"tier {tier}")
            for node in g.nodes.values():
                self.assertEqual(node.needed, g.degree(node.id), f"tier {tier} {node.id}")

    def test_tags_are_unique_because_a_third_portal_breaks_the_pair(self):
        for tier in sorted(self.cat.tiers):
            g = portals.build(self.cat, tier)
            tags = [e.tag for e in g.edges]
            self.assertEqual(len(tags), len(set(tags)), f"tier {tier}")

    def test_graph_is_connected_and_within_four_hops(self):
        for tier in sorted(self.cat.tiers):
            g = portals.build(self.cat, tier)
            if not g.nodes:
                continue
            hops = g.max_hops()
            self.assertGreaterEqual(hops, 0, f"tier {tier} is disconnected")
            self.assertLessEqual(hops, 4, f"tier {tier} needs {hops} hops")

    def test_no_hub_exceeds_its_portal_capacity(self):
        cap = int(POLICY["portals"]["max_spokes_per_hub"])
        for tier in sorted(self.cat.tiers):
            g = portals.build(self.cat, tier)
            for hub in g.hubs():
                self.assertLessEqual(g.degree(hub.id), cap, f"tier {tier} {hub.id}")
            self.assertEqual([w for w in g.warnings if "full" in w], [], f"tier {tier}")

    def test_shortfall_is_reported_for_hubs_whose_blueprint_has_no_portals(self):
        # MEASURED: god-portaaoo.blueprint and Portal13.vbuild contain 0 portal pieces
        # despite being the already-selected "portal" placements. Assuming a building
        # called a portal hub has portals in it is how you place a hub that cannot
        # teleport anybody.
        g = portals.build(self.cat, 9)
        text = " ".join(g.shortfalls)
        self.assertIn("sandbox-portal-hub", text)
        self.assertIn("deepnorth-landing-portal", text)
        node = g.nodes["sandbox-portal-hub"]
        self.assertEqual(node.contained, 0)
        self.assertEqual(node.shortfall, node.needed)


if __name__ == "__main__":
    unittest.main()
