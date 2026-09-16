#!/usr/bin/env python3
"""No loose fixture may stand inside the building -- for every preset, every site.

THE DEFECT THIS EXISTS FOR
--------------------------
Reported from live play on Ulfsland on 2026-09-15, by an operator walking up to
the finished `pre-bonemass/iron-era-workshop`: "the position isnt correct, there
are portals in the middle of walls".

MEASURED, box-testing what `terraform/stock.py` placed against all 1,906 solids
of the blueprint body placed before it: 12 of its 18 fixtures were INSIDE the
structure. `portal_wood` at pad-offset (-6.00, 0.00) overlapped a
`stone_wall_4x2` by 1.00 x 2.00 x 1.18 m. `piece_maypole` pierced 22 solids,
`smelter` 20, `piece_workbench` 11. The minimum fixture-to-wall gap across the
set was 0.000 m.

Cause: `stock.py` placed from hardcoded offsets relative to the PAD CENTRE,
solved when the body was anchored on its own corner and the middle of the pad
was empty ground. `to_rcon_plan.py` was then fixed to centre the body on its
measured footprint, the building moved onto the middle of the pad, and the
offsets did not follow.

WHY IT IS A TEST AND NOT A REPORT
---------------------------------
The operator found this by walking into it. Nothing in the pipeline was asking
the question, so the answer was never wrong -- it was absent. This test asks it
for every placement of every preset, offline, from the same transform the
emitter uses, and FAILS rather than warning: a portal inside a wall is not a
degraded placement, it is a broken one.

The failure message carries the measured overlap, because "fixture collides"
does not tell an operator which fixture, in what, or by how much.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent
JUMPSTART = TOOLS / "jumpstart"
BLUEPRINTS = JUMPSTART / "blueprints"
TERRAFORM = JUMPSTART / "terraform"
sys.path.insert(0, str(BLUEPRINTS))
sys.path.insert(0, str(TERRAFORM))

import base_geometry as bg  # noqa: E402
import data_entry  # noqa: E402
import fixtures  # noqa: E402
import stock  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402

WORLDS = JUMPSTART / "worlds"


def placements() -> list[tuple[str, str, dict]]:
    """(world, preset, placement) for everything that has a solved site."""
    out = []
    for world_dir in sorted(WORLDS.iterdir()):
        if not world_dir.is_dir():
            continue
        for preset_dir in sorted(world_dir.iterdir()):
            doc = preset_dir / "placements.yaml"
            if not doc.is_file():
                continue
            loaded = yaml.safe_load(doc.read_text())
            for place in loaded.get("placements") or []:
                if place.get("solved"):
                    out.append((world_dir.name, preset_dir.name, place))
    return out


class FixtureGeometry(unittest.TestCase):
    """The primitives the guard is built on, asserted so a wrong box test cannot
    quietly pass everything."""

    @classmethod
    def setUpClass(cls):
        cls.g = bg.geometry()

    def test_overlapping_boxes_report_their_penetration(self):
        a = (0.0, 2.0, 0.0, 1.0, 0.0, 2.0, "a")
        b = (1.5, 3.0, 0.5, 2.0, 1.0, 4.0, "b")
        ox, oy, oz = fixtures.overlap(a, b)
        self.assertAlmostEqual(ox, 0.5)
        self.assertAlmostEqual(oy, 0.5)
        self.assertAlmostEqual(oz, 1.0)
        self.assertEqual(fixtures.gap(a, b), 0.0)

    def test_touching_boxes_do_not_count_as_intersecting(self):
        a = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0, "a")
        b = (1.0, 2.0, 0.0, 1.0, 0.0, 1.0, "b")
        self.assertLessEqual(fixtures.overlap(a, b)[0], 0.0)
        self.assertEqual(fixtures.gap(a, b), 0.0)

    def test_separated_boxes_report_their_distance(self):
        a = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0, "a")
        b = (4.0, 5.0, 0.0, 1.0, 0.0, 1.0, "b")
        self.assertAlmostEqual(fixtures.gap(a, b), 3.0)

    def test_a_fixture_box_stands_on_the_surface_it_is_given(self):
        # Every ground-resting prefab's solid starts at its own pivot, which is
        # why a prop spawned at the pad height rests on the pad.
        box = fixtures.fixture_box("piece_chest_wood", 10.0, 70.91, -5.0, 0.0, self.g)
        self.assertAlmostEqual(box[2], 70.91, places=2)
        self.assertGreater(box[3], box[2])

    def test_the_fixture_role_set_catches_kit_variants(self):
        for prefab in ("piece_chest_wood", "piece_chest_blackmetal", "portal_wood",
                       "charcoal_kiln", "piece_workbench", "hearth", "bed",
                       "piece_maypole", "fire_pit"):
            with self.subTest(prefab=prefab):
                self.assertTrue(fixtures.is_fixture(prefab))
        for prefab in ("stone_wall_2x1", "wood_floor", "stone_floor_2x2", "wood_roof"):
            with self.subTest(prefab=prefab):
                self.assertFalse(fixtures.is_fixture(prefab))


class FixturesClearTheStructure(unittest.TestCase):
    """The guard itself, over every solved placement in the repository."""

    @classmethod
    def setUpClass(cls):
        cls.cases = placements()
        cls.g = bg.geometry()

    def test_there_are_placements_to_check(self):
        # A guard that silently checks nothing is the failure mode this whole
        # module is a reaction to.
        self.assertGreaterEqual(len(self.cases), 8)

    def test_no_planned_fixture_stands_inside_its_building(self):
        checked = 0
        for world, preset, place in self.cases:
            with self.subTest(site=f"{preset}/{place['id']}"):
                report = json.loads(subprocess.run(
                    [sys.executable, str(TERRAFORM / "stock.py"),
                     "--world", world, "--preset", preset, "--id", place["id"],
                     "--plan", "--portal-tag", "u-test"],
                    capture_output=True, text=True, check=True).stdout)
                audit = report.get("audit")
                if audit is None:
                    self.assertIsNone(report["plan"]["blueprint"],
                                      "a placement with a blueprint must be audited")
                    continue
                checked += 1
                self.assertEqual(
                    audit["failures"], [],
                    f"{preset}/{place['id']}: " + "; ".join(audit["failures"]))
                self.assertEqual(
                    audit["preference_failures"], [],
                    f"{preset}/{place['id']}: " + "; ".join(audit["preference_failures"]))
                self.assertEqual(
                    audit["portal_failures"], [],
                    f"{preset}/{place['id']}: " + "; ".join(audit["portal_failures"]))
                # A prop or chest with NO declared preference and nowhere legal
                # to stand is a site that cannot be stocked, so it fails --
                # that is `fatal`. An EXTENSION with nowhere to stand is a
                # station the blueprint's own builder walled in, and a fixture
                # whose declared PREFERENCE this body cannot satisfy (a bed
                # wants a covered indoor cell; a dock has none) is a fact about
                # the body. Both are reported and neither is silently moved
                # somewhere legal-but-wrong, which is what this whole change is
                # about: the yard fallback was the defect.
                hard = [u for u in report["unplaceable"] if u["fatal"]]
                self.assertEqual(hard, [],
                                 f"{preset}/{place['id']}: nowhere legal to stand")
                for row in report["unplaceable"]:
                    self.assertIn("want", row)
                for row in report["plan"]["preference_unplaceable"]:
                    # loud means SAYING WHAT and WHY, not just omitting it
                    self.assertIn(row["want"], (fixtures.WANT_INDOOR,
                                                fixtures.WANT_OUTDOOR))
                    self.assertTrue(row["reason"])
                self.assertGreater(audit["min_wall_clearance_m"], 0.0,
                                   f"{preset}/{place['id']}: a fixture touches a wall")
        self.assertGreater(checked, 0, "no placement was actually audited")

    def test_a_station_the_blueprint_carries_is_not_placed_again(self):
        # The duplication half of the defect: nine of the ten station spawns on
        # iron-era-workshop were second copies standing in the walls of the
        # first, because the preset names them and the body already had them.
        #
        # Asserted against whatever the placement CARRIES rather than against a
        # particular body's inventory. The pinned version read
        # `carried["portal_wood"] == 1` and `carried["piece_chest_wood"] == 17`,
        # which are facts about `BjOrN_blueprint001`, and it failed the moment
        # the placement was pointed at a body that grounds properly -- reporting
        # a body swap as a duplication bug. The contract is: whatever the body
        # carries, stock does not place a second one, and the decision is
        # declared.
        report = json.loads(subprocess.run(
            [sys.executable, str(TERRAFORM / "stock.py"),
             "--world", "Ulfsland", "--preset", "pre-bonemass",
             "--id", "iron-era-workshop", "--plan", "--portal-tag", "u-workshop"],
            capture_output=True, text=True, check=True).stdout)
        plan = report["plan"]
        carried = plan["carried_by_blueprint"]
        self.assertTrue(carried, "the body carries nothing, so nothing is tested")
        owned = 0
        for row in plan["stations"]:
            with self.subTest(station=row["prefab"]):
                if row["carried_by_blueprint"]:
                    owned += 1
                    self.assertIsNone(row["place"],
                                      f"{row['prefab']} is in the blueprint already")
                    self.assertEqual(row["owner"], "blueprint")
                else:
                    self.assertEqual(row["owner"], "stock")
        self.assertGreater(owned, 0, "no station is owned by the blueprint")
        # Nothing stock places may duplicate a prefab the body already has, or
        # one this same run has already placed as one of the preset's STATIONS
        # -- MEASURED, `portal_wood` is both a `PROPS` entry and a station in
        # every preset that names one, so a site whose body carries no portal
        # used to get two, 14 m apart. `PROPS` deliberately lists `bed` twice,
        # so this is not a global uniqueness claim: it is specifically that the
        # prop list and the placed-station list do not overlap.
        props = [p["prefab"] for p in plan["props"]]
        stations = [r["prefab"] for r in plan["stations"] if r.get("place")]
        for prefab in props + stations:
            self.assertFalse(carried.get(prefab),
                             f"{prefab} is placed although the body carries it")
        self.assertEqual(sorted(set(props) & set(stations)), [],
                         f"placed as both a prop and a station: props={props} "
                         f"stations={stations}")
        self.assertEqual(len(stations), len(set(stations)),
                         f"a station prefab is placed twice: {stations}")
        for entry in plan["skipped"]:
            self.assertIn("reason", entry)

    def test_the_station_levels_are_still_topped_up(self):
        # Ownership must not cost the preset its station LEVELS. The old version
        # asserted the exact numbers of one body (a workbench with two
        # extensions, so two more needed); this asserts the ARITHMETIC over
        # every solved placement in the repository, and that every extension it
        # decides to add gets a real position beside the station it belongs to.
        checked = 0
        for world, preset, place in self.cases:
            report = json.loads(subprocess.run(
                [sys.executable, str(TERRAFORM / "stock.py"),
                 "--world", world, "--preset", preset, "--id", place["id"],
                 "--plan", "--portal-tag", "u-test"],
                capture_output=True, text=True, check=True).stdout)
            pad_y = float((place["solved"].get("flatten_cost") or {}).get("target_y")
                          or place["solved"]["y"])
            for row in report["plan"]["stations"]:
                with self.subTest(preset=preset, id=place["id"],
                                  station=row["prefab"]):
                    want = max(0, row["required_level"] - 1
                               - row["extensions_carried"])
                    self.assertEqual(row["extensions_needed"], want)
                    self.assertEqual(len(row["extensions"]), want)
                    checked += 1
                    for ext in row["extensions"]:
                        if ext["place"] is None:
                            # A walled-in station is a fact about the body and
                            # is reported, not a placement bug -- see the audit
                            # test above.
                            continue
                        # The GAME's rule, MEASURED out of the deployed
                        # `assembly_valheim.dll` rather than pinned from
                        # observation: `StationExtension::.ctor` sets
                        # `m_maxStationDistance = 5.0` and
                        # `CraftingStation::FindStationsInRange` accepts a
                        # station when `Vector3.Distance(station.position,
                        # center) < maxRange`, strict. The previous bound here
                        # was 3.6, which was the largest distance the search
                        # happened to produce on the bodies of the day -- an
                        # observation, not a rule, and it failed the moment a
                        # body needed 3.717 m. An extension is placed at its
                        # station's own Y, so this horizontal distance IS the
                        # 3-D one the game measures.
                        self.assertLess(ext["distance_m"],
                                        stock.MAX_STATION_DISTANCE_M)
                        self.assertTrue(ext["within_game_range"])
                        if row["owner"] == "blueprint":
                            # beside the blueprint's own station, at ITS floor,
                            # which is the whole point of not re-placing it
                            self.assertGreaterEqual(ext["place"]["y"], pad_y)
        self.assertGreater(checked, 0, "no station levels were checked")


class TheDefectItself(unittest.TestCase):
    """The offsets that were live in the world, asserted to be caught.

    This is the regression test proper: it re-creates what `stock.py` placed on
    `iron-era-workshop` and requires the audit to reject it. Before the fix
    `stock.py` had no audit at all, so this could not have been asked.
    """

    BODY = Path("/media/big4/projects/game/valheim/old/old/old/old_Storgard/"
                "config_merged/BepInEx/PlanBuild/blueprints/"
                "BjOrN_blueprint001.blueprint")
    # what was in the world, MEASURED with `findObjects -prefab ... -near`:
    # every one of these sat at exactly the pad height with a round pad offset.
    WAS_PLACED = [
        ("piece_workbench", 10.0, 10.0), ("forge", 5.0, 10.0),
        ("piece_cauldron", 0.0, 10.0), ("smelter", -5.0, 10.0),
        ("charcoal_kiln", -10.0, 10.0), ("piece_chest_wood", -15.0, 10.0),
        ("portal_wood", -20.0, 10.0), ("hearth", -25.0, 10.0),
        ("piece_stonecutter", -30.0, 10.0), ("portal_wood", -6.0, 0.0),
        ("fire_pit", 0.0, -4.0), ("bed", 4.0, -4.0), ("bed", 6.0, -4.0),
        ("piece_cartographytable", -4.0, 4.0), ("piece_maypole", 0.0, -8.0),
        ("piece_chest", -1.6, -12.0), ("piece_chest", 0.0, -12.0),
        ("piece_chest", 1.6, -12.0),
    ]
    CX, CZ, PAD = -4673.5, -310.5, 70.91

    @classmethod
    def setUpClass(cls):
        if not cls.BODY.is_file():
            raise unittest.SkipTest(f"{cls.BODY} not present")
        cls.objs = [o for o in read_objects(cls.BODY) if o.prefab != "piece_Sundial"]

    def _audit(self, base_y):
        body = fixtures.place_body(self.objs, at=(self.CX, self.PAD, self.CZ),
                                   yaw_deg=180.0, base_y=base_y)
        return body, fixtures.audit(
            [(p, self.CX + dx, self.PAD, self.CZ + dz, 180.0)
             for p, dx, dz in self.WAS_PLACED], body)

    def test_the_live_offsets_are_rejected_with_the_measured_overlap(self):
        _body, audit = self._audit(1.5)   # the datum the world was placed with
        self.assertEqual(audit["min_clearance_m"], 0.0)
        self.assertEqual(len(audit["failures"]), 12,
                         "12 of the 18 fixtures stood inside the building")
        portal = [f for f in audit["failures"]
                  if f.startswith("portal_wood") and "-4679.50" in f]
        self.assertEqual(len(portal), 1, audit["failures"])
        self.assertIn("overlaps stone_wall_4x2 by 1.00 x 2.00 x 1.18 m", portal[0])

    def test_the_body_transform_matches_the_emitter(self):
        # The guard is only as good as the building it tests against. These are
        # positions read back from the LIVE server with `findObjects`, and the
        # reconstruction has to land on them.
        body = fixtures.place_body(self.objs, at=(self.CX, self.PAD, self.CZ),
                                   yaw_deg=180.0, base_y=1.5)
        ox, oy, oz = body.origin
        live = {
            "portal_wood": (-4671.92, 82.34, -310.68),
            "forge": (-4689.45, 75.52, -323.47),
            "hearth": (-4663.08, 75.24, -317.10),
            "piece_stonecutter": (-4655.50, 71.53, -327.46),
            "piece_cartographytable": (-4665.45, 78.32, -310.38),
        }
        for prefab, (lx, ly, lz) in live.items():
            with self.subTest(prefab=prefab):
                best = min(
                    ((-o.pos[0] + ox - lx) ** 2 + (o.pos[1] + oy - ly) ** 2
                     + (-o.pos[2] + oz - lz) ** 2) ** 0.5
                    for o in self.objs if o.prefab == prefab)
                self.assertLess(best, 0.02, f"{prefab} reconstructed {best:.3f} m off")


class FixturesGoWhereAPersonWouldPutThem(unittest.TestCase):
    """The SECOND defect, reported from live play once the first was fixed:

      "i noticed that there were hearth and portal and some things outside the
       building... is this intentional?"

    MEASURED live at the time, all at pad height, against a body whose walls end
    ~12 m from the pad centre: `portal_wood` 15.0 m out, `hearth` 15.8,
    `fire_pit` 12.6, both `bed`s 12.8 and 12.6, `piece_maypole` 13.0. Every one
    of them cleared every solid by more than 0.5 m, so the intersection guard
    above passed them all. The guard was answering the only question anyone had
    asked. These are the questions nobody had asked.
    """

    SITE = ("Ulfsland", "pre-bonemass", "iron-era-workshop")

    @classmethod
    def setUpClass(cls):
        place = stock.load_placement(*cls.SITE)
        solved = place["solved"]
        cost = solved.get("flatten_cost") or {}
        cls.cx, cls.cz = float(solved["x"]), float(solved["z"])
        cls.pad_y = float(cost.get("target_y") or solved["y"])
        cls.yaw = float((place.get("rotation") or {}).get("yaw") or 0.0)
        cls.body = stock.load_body(place, cls.cx, cls.cz, cls.pad_y, cls.yaw)
        if cls.body is None:
            raise unittest.SkipTest(f"{cls.SITE} names no blueprint body")
        cls.mask = cls.body.interior()
        cls.index = cls.body.index()

    # --- the mask itself

    def test_the_mask_classifies_every_cell_of_the_raster_exactly_once(self):
        klasses = set(self.mask.klass.values())
        self.assertTrue(klasses <= {fixtures.OUTDOOR, fixtures.INDOOR_COVERED,
                                    fixtures.INDOOR_UNCOVERED,
                                    fixtures.COVERED_UNENCLOSED,
                                    fixtures.UNREACHABLE, fixtures.BLOCKED},
                        klasses)
        self.assertEqual(sum(self.mask.counts().values()), len(self.mask.klass))
        ix0, ix1, iz0, iz1 = self.mask.bounds
        self.assertEqual(len(self.mask.klass), (ix1 - ix0 + 1) * (iz1 - iz0 + 1))

    def test_this_body_has_an_interior_and_the_mask_finds_it(self):
        # MEASURED on halvar-master-refinery at its solved pad: 1,443 cells at
        # 1 m, of which 37 are indoor and covered. The figure is not asserted
        # exactly -- it is a property of a geometry dump that can be refreshed --
        # but its EXISTENCE is the whole claim, and a mask that finds no interior
        # in a 917-piece two-storey stone refinery is broken.
        indoor = self.mask.of_class(fixtures.INDOOR_COVERED)
        self.assertGreater(len(indoor), 10, self.mask.render())
        for cell in indoor:
            self.assertGreaterEqual(self.mask.cover_m[cell], self.mask.headroom_m,
                                    f"cell {cell} is 'covered' by something below "
                                    f"head height")

    def test_an_eave_is_covered_and_is_not_indoors(self):
        # The distinction that makes the mask worth having. MEASURED: this body's
        # roof overhangs its walls, so there are cells with a roof above them and
        # no walls around them. A bed there is a bed on a porch.
        eaves = self.mask.of_class(fixtures.COVERED_UNENCLOSED)
        self.assertGreater(len(eaves), 0, self.mask.render())
        for cell in eaves:
            self.assertIn(cell, self.mask.cover_m)
            self.assertNotIn(cell, self.mask.of_class(fixtures.INDOOR_COVERED))

    def test_space_no_player_can_reach_is_found_and_never_offered(self):
        # MEASURED on this body: 7 cells are free of solids and have no path of
        # free cells to the rim of the raster -- the 1 m slots between its
        # smelters and kilns. A fixture in one exists and cannot be used, which
        # is the same class of defect as the portal with no return: the check
        # passes, the thing does not work.
        pockets = self.mask.of_class(fixtures.UNREACHABLE)
        self.assertGreater(len(pockets), 0, self.mask.render())
        # Ask for a chest AT the pocket. The search must not hand it back.
        for cell in pockets:
            px, pz = self.mask.world(*cell)
            found = fixtures.free_spot(
                "piece_chest", self.body, self.index, (self.cx, self.cz),
                self.pad_y, 35.0, prefer=(px, pz), yaw_deg=self.yaw,
                mask=self.mask)
            if found is None:
                continue
            self.assertNotEqual((found["offset"][0], found["offset"][1]),
                                (cell[0] * 1.0, cell[1] * 1.0),
                                f"a fixture was placed in unreachable cell {cell}")
            self.assertNotEqual(found["class"], fixtures.UNREACHABLE)

    # --- the preference

    def test_the_bed_that_was_in_the_rain_is_now_a_failure(self):
        # The operator's own MEASURED position for one of the two beds. It
        # intersects nothing -- `failures` is empty -- and it is still wrong,
        # which is exactly why `preference_failures` is a separate list.
        audit = fixtures.audit([("bed", -4665.50, self.pad_y, -320.50, 180.0)],
                               self.body)
        self.assertEqual(audit["failures"], [],
                         "the live bed position was legal; that was the problem")
        self.assertEqual(len(audit["preference_failures"]), 1,
                         audit["preference_failures"])
        message = audit["preference_failures"][0]
        self.assertIn("bed", message)
        self.assertIn(fixtures.WANT_INDOOR, message)
        self.assertIn("open to the sky", message)

    def test_a_maypole_indoors_is_also_a_failure(self):
        # The preference is a preference, not a synonym for "inside". A fixture
        # declared outdoor has to be outdoors, or the rule is just a new global.
        indoor = sorted(self.mask.of_class(fixtures.INDOOR_COVERED))
        x, z = self.mask.world(*indoor[0])
        audit = fixtures.audit([("piece_maypole", x, self.pad_y, z, 0.0)],
                               self.body)
        self.assertTrue(any(fixtures.WANT_OUTDOOR in m
                            for m in audit["preference_failures"]),
                        audit["preference_failures"])

    def test_the_planner_puts_the_bed_indoors_under_a_roof_beside_a_wall(self):
        report = json.loads(subprocess.run(
            [sys.executable, str(TERRAFORM / "stock.py"),
             "--world", self.SITE[0], "--preset", self.SITE[1],
             "--id", self.SITE[2], "--plan", "--portal-tag", "u-workshop"],
            capture_output=True, text=True, check=True).stdout)
        beds = [r for r in report["audit"]["fixtures"] if r["prefab"] == "bed"]
        self.assertEqual(len(beds), 1,
                         "this body has room for exactly one bed indoors; the "
                         "second is reported, not stood in the yard")
        bed = beds[0]
        self.assertEqual(bed["class"], fixtures.INDOOR_COVERED)
        # EVERY cell the bed's box spans must be DRY, and its anchor cell must
        # be indoors. Not "spans indoor_covered only": a bed beside a wall is
        # 1.2 x 2.8 m and legitimately reaches into an adjoining
        # `covered_unenclosed` cell - under the same roof, outside the enclosure
        # set that ENCLOSURE deliberately computes separately from
        # traversability. The operator's complaint was a bed IN THE RAIN, so
        # dryness is the contract; demanding a single class failed a dry bed.
        self.assertTrue(
            set(bed["spans"]) <= {fixtures.INDOOR_COVERED, fixtures.COVERED_UNENCLOSED},
            f"bed spans a cell that is not covered: {bed['spans']}")
        # near but NOT touching: the objective minimises wall distance subject
        # to the clearance floor, so it has to be both.
        self.assertGreater(bed["wall_gap_m"], 0.0)
        self.assertLess(bed["wall_gap_m"], 1.5)
        # and what could not be housed is SAID, not moved outdoors. Counted
        # rather than name-tested, because `PROPS` asks for TWO beds and only
        # one fits: the prefab appears in both lists, and the claim is that
        # placed + refused equals asked-for.
        refused = [r["prefab"] for r in report["plan"]["preference_unplaceable"]]
        self.assertTrue(refused, "nothing was reported, so nothing was refused")
        placed = [r["prefab"] for r in report["audit"]["fixtures"]]
        for prefab in set(refused):
            self.assertEqual(fixtures.preference(prefab), fixtures.WANT_INDOOR)
        self.assertEqual(refused.count("bed") + placed.count("bed"), 2)
        for prefab in ("hearth",):
            # a single-instance indoor fixture this body cannot house: refused
            # loudly and NOT standing in the rain
            self.assertIn(prefab, refused)
            self.assertNotIn(prefab, placed)
        # Scarce indoor space goes to the BED, not to whatever is listed first.
        # MEASURED on this body: one indoor region can take a `bed` and the same
        # region is the only one that can take a `fire_pit`, so the order in
        # `stock.PROPS` decides it -- and it is ordered deliberately, because a
        # bed is a spawn point and a fire pit is decor.
        self.assertIn("fire_pit", refused)
        self.assertNotIn("fire_pit", placed)
        self.assertLess([p for p, _o, _y in stock.PROPS].index("bed"),
                        [p for p, _o, _y in stock.PROPS].index("fire_pit"))

    def test_standing_on_a_floor_is_not_colliding_with_it(self):
        # The reason every hearth and fire pit ended up outdoors. MEASURED:
        # `fire_pit`'s collider starts 0.73 m BELOW its pivot, so on any floor
        # slab it overlaps by 0.73 m. On bare pad there is nothing to overlap,
        # which is why the yard was the only place it ever passed.
        span = bg.geometry().local_aabb("fire_pit")
        self.assertLess(span[1], -0.5, "fire_pit is no longer a sunken prefab; "
                                       "this test's premise needs re-measuring")
        indoor = sorted(self.mask.of_class(fixtures.INDOOR_COVERED))
        rested = 0
        for cell in indoor:
            x, z = self.mask.world(*cell)
            box = fixtures.fixture_box("fire_pit", x, self.pad_y, z, 0.0)
            hits, supports = fixtures.probe(box, self.index, stand_y=self.pad_y)
            floors = [s for s in supports if s["prefab"].startswith("stone_floor")]
            if not floors:
                continue
            rested += 1
            self.assertGreater(floors[0]["penetration_m"], 0.5)
            self.assertNotIn(floors[0]["prefab"], [h["prefab"] for h in hits])
        self.assertGreater(rested, 0, "no indoor cell has a floor under it, so "
                                      "the support rule was never exercised")


class PortalsHaveEndpoints(unittest.TestCase):
    """The THIRD defect: "i went through portal and on the other side there
    wasnt a portal to return with. i dont know how a portal could work without
    another endpoint."

    MEASURED from the deployed `assembly_valheim.dll`:
    `Game::FindRandomUnconnectedPortal` keeps a candidate when
    `zdo.GetString(ZDOVars.s_tag, "") == tag` and its portal connection is
    `ZDOID.None`, then returns `list[Random.Range(0, list.Count)]`. Tag equality
    is exact string equality, `""` included, and the partner among equals is a
    UNIFORM RANDOM DRAW. MEASURED in this world: 26 blank-tag `portal_wood`
    instances stand in mod-added world locations, so an untagged portal at a site
    is a random one-way door to one of them.
    """

    def test_no_tag_is_refused_with_the_reason(self):
        for tag in (None, "", "   "):
            with self.subTest(tag=tag):
                self.assertIsNotNone(fixtures.portal_tag_problem(tag))

    def test_a_tag_no_player_could_retype_is_refused(self):
        # MEASURED: `TeleportWorld::Interact` calls
        # `TextInput::RequestText(this, "$piece_portal_tag", 10)`. A longer tag
        # pairs, but a broken pair could never be repaired by hand.
        self.assertEqual(fixtures.PORTAL_TAG_MAX_CHARS, 10)
        problem = fixtures.portal_tag_problem("uls-ironworksh")
        self.assertIsNotNone(problem)
        self.assertIn("10", problem)
        self.assertIsNone(fixtures.portal_tag_problem("u-workshop"))

    def test_a_tag_pairing_could_not_match_is_refused(self):
        # Pairing is raw string equality, so a character a player cannot type
        # identically is a pair that never forms and cannot be diagnosed.
        for tag in ("u workshop", "u/workshop", "u-wörkshop"):
            with self.subTest(tag=tag):
                self.assertIsNotNone(fixtures.portal_tag_problem(tag))

    def test_an_untagged_portal_in_a_plan_is_a_build_failure(self):
        place = stock.load_placement("Ulfsland", "pre-bonemass",
                                     "iron-era-workshop")
        solved = place["solved"]
        pad_y = float((solved.get("flatten_cost") or {}).get("target_y")
                      or solved["y"])
        body = stock.load_body(place, float(solved["x"]), float(solved["z"]),
                               pad_y, 180.0)
        at = (float(solved["x"]) + 12.0, pad_y, float(solved["z"]))
        untagged = fixtures.audit([("portal_wood", at[0], at[1], at[2], 0.0)],
                                  body)
        self.assertEqual(len(untagged["portal_failures"]), 1,
                         untagged["portal_failures"])
        self.assertIn("Random.Range", untagged["portal_failures"][0])
        tagged = fixtures.audit(
            [("portal_wood", at[0], at[1], at[2], 0.0, "u-workshop")], body)
        self.assertEqual(tagged["portal_failures"], [])

    def test_stock_places_no_portal_at_all_without_a_tag(self):
        # Asked of the PLANNER directly rather than of the CLI, because the
        # placements now carry `portal_tag:` as durable data -- so the CLI has a
        # tag even with no flag, which is the intended behaviour and would make
        # a CLI-level "no tag" test quietly test nothing.
        world, preset, pid = "Ulfsland", "pre-bonemass", "iron-era-workshop"
        place = stock.load_placement(world, preset, pid)
        solved = place["solved"]
        cx, cz = float(solved["x"]), float(solved["z"])
        pad_y = float((solved.get("flatten_cost") or {}).get("target_y")
                      or solved["y"])
        yaw = float((place.get("rotation") or {}).get("yaw") or 0.0)
        preset_doc = stock.load_preset(preset)
        self.assertTrue(
            any(s["prefab"] == "portal_wood"
                for s in preset_doc.get("stations") or []),
            "this preset asks for no portal, so the refusal is untested")

        without = stock.solve_placement(place, preset_doc, 2, cx, cz, pad_y, yaw,
                                        35.0, portal_tag=None)
        self.assertIsNotNone(without["portal"]["problem"])
        planned = [r["prefab"] for r in without["stations"] if r.get("place")]
        planned += [r["prefab"] for r in without["props"] if r.get("place")]
        self.assertEqual([p for p in planned if fixtures.is_portal(p)], [],
                         "a portal was planned with no tag")
        refusals = [s for s in without["skipped"]
                    if fixtures.is_portal(s["prefab"])]
        self.assertTrue(refusals, "the refusal was silent")
        self.assertIn("blank", refusals[0]["reason"])

        with_tag = stock.solve_placement(place, preset_doc, 2, cx, cz, pad_y, yaw,
                                         35.0, portal_tag="u-workshop")
        self.assertIsNone(with_tag["portal"]["problem"])
        placed = [r for r in with_tag["stations"] + with_tag["props"]
                  if r.get("place") and fixtures.is_portal(r["prefab"])]
        self.assertEqual(len(placed), 1, "exactly one portal per site")
        self.assertEqual(placed[0]["place"]["tag"], "u-workshop")

    def test_the_placement_file_is_the_durable_source_of_the_tag(self):
        # A tag passed on a command line is a tag somebody can forget. The
        # placement carries it, and the report says which source was used so a
        # mismatch is visible rather than silent.
        report = json.loads(subprocess.run(
            [sys.executable, str(TERRAFORM / "stock.py"),
             "--world", "Ulfsland", "--preset", "pre-bonemass",
             "--id", "iron-era-workshop", "--plan"],
            capture_output=True, text=True, check=True).stdout)
        portal = report["plan"]["portal"]
        self.assertEqual(portal["tag_source"], "placement")
        self.assertIsNone(portal["problem"], portal)
        self.assertIsNone(fixtures.portal_tag_problem(portal["tag"]))
        portals = [r for r in report["audit"]["fixtures"]
                   if fixtures.is_portal(r["prefab"])]
        self.assertEqual(len(portals), 1)
        self.assertEqual(portals[0]["tag"], portal["tag"])

    def test_the_tag_is_written_as_the_zdo_string_the_game_pairs_on(self):
        # The mechanism, asserted end to end: the base64 `data=` payload decodes
        # back to a ZDO `tag` string, which is the field
        # `FindRandomUnconnectedPortal` reads. Without this the tag would be a
        # number in a report and nothing in the world.
        blob = fixtures.portal_data("u-workshop")
        entry = data_entry.decode(blob)
        self.assertEqual(entry.get_string("tag"), "u-workshop")
        command = fixtures.portal_spawn_command("portal_wood", -4672.5, 70.91,
                                                -325.5, 90.0, "u-workshop")
        # `spawn_object`, never ValheimRcon's `spawn`: the latter takes no data
        # payload, so it can only ever produce the blank-tag portal.
        self.assertTrue(command.startswith("spawn_object portal_wood "), command)
        self.assertIn(f"data={blob}", command)
        self.assertIn("from=0,0,0", command)

    def test_a_tagged_portal_cannot_pair_with_a_world_location_portal(self):
        # The property the scheme exists for. World-location portals are blank,
        # and pairing is exact string equality, so a non-empty tag can never
        # match one. Asserted as the rule rather than as a scan of the world,
        # because the rule is what holds for the 11 sites not yet built.
        self.assertIsNone(fixtures.portal_tag_problem("u-workshop"))
        self.assertNotEqual("u-workshop", "")
        self.assertIsNotNone(fixtures.portal_tag_problem(""))


if __name__ == "__main__":
    unittest.main()
