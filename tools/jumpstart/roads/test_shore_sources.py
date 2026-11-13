"""Installation service identity comes from its canonical written pad, not a sandbar."""
import unittest

import numpy as np
import shores as S
from shore_connectivity import CompleteLand


class InstallationSourceTests(unittest.TestCase):
    def setUp(self):
        coords = np.arange(-16, 17, dtype=float)
        x, z = np.meshgrid(coords, coords)
        heights = np.where(x >= 6, 31.0, 20.0)
        heights[(x >= 1) & (x <= 2) & (abs(z) <= 1)] = 31.0
        field = S.W.Field('source-contact', 0., 0., 16.5, 1., 33,
                           heights, np.ones(heights.shape, dtype=np.uint8))
        self.complete = CompleteLand(field, 12)
        self.footprint = dict(owner='p#early-dock', xz=[0., 0.],
                              width_m=16., depth_m=4., network_root_xz=[10., 0.])

    def test_rooted_pad_contact_serves_wet_centre_beside_foreign_sandbar(self):
        from shore_sources import footprint_contact
        contact = footprint_contact(self.complete, self.footprint)
        self.assertEqual(contact['component'], self.complete.at(10., 0.))
        self.assertNotEqual(contact['component'], self.complete.at(1., 0.))
        self.assertEqual(self.complete.at(0., 0.), 0)
        self.assertGreater(contact['samples'], 0)
        rooted = {tuple(point) for point in contact['rooted_samples_xz']}
        self.assertEqual(len(rooted), contact['samples'])
        self.assertTrue(all(self.complete.at(*point) == contact['component']
                            for point in rooted))
        self.assertIn((6, 0), rooted)
        self.assertIn((8, 0), rooted)
        self.assertNotIn((1, 0), rooted)

    def test_foreign_land_without_rooted_contact_is_not_authorization(self):
        from shore_sources import footprint_contact
        footprint = dict(self.footprint, width_m=4.)
        with self.assertRaisesRegex(SystemExit, 'contact'):
            footprint_contact(self.complete, footprint)

    def test_contact_uses_actual_writer_edge_tolerance(self):
        from shore_sources import footprint_contact
        boundary = dict(self.footprint, width_m=11.5)
        self.assertEqual(footprint_contact(self.complete, boundary)['component'],
                         self.complete.at(10., 0.))
        with self.assertRaisesRegex(SystemExit, 'contact'):
            footprint_contact(self.complete, dict(boundary, width_m=11.4998))

    def test_unread_network_root_cannot_authorize_a_different_island(self):
        from shore_sources import footprint_contact
        with self.assertRaisesRegex(SystemExit, 'root'):
            footprint_contact(self.complete,
                              dict(self.footprint, network_root_xz=[0., 0.]))

    def test_duplicate_cannot_expand_the_canonical_physical_pad(self):
        from shore_sources import installation_footprints, footprint_contact
        owner = dict(id='early-dock', _qualified='p#early-dock',
                     solved=dict(x=0., z=0., footprint_orientation=
                                 '4 m along x by 4 m along z'))
        duplicate = dict(id='sandbox-harbour', _qualified='a#sandbox-harbour',
                         _duplicate_of='p#early-dock',
                         solved=dict(x=0., z=0., footprint_orientation=
                                     '24 m along x by 24 m along z'))
        for records in ([duplicate, owner], [owner, duplicate]):
            with self.subTest(order=[p['_qualified'] for p in records]):
                footprint = installation_footprints(
                    {'dockshore': (0., 0.), 'portalhub': (10., 0.)},
                    placements=records)['dockshore']
                self.assertEqual(footprint['owner'], 'p#early-dock')
                with self.assertRaisesRegex(SystemExit, 'contact'):
                    footprint_contact(self.complete, footprint)

    def test_distinct_canonical_owners_for_one_installation_are_refused(self):
        from shore_sources import installation_footprints
        records = [dict(id=n, _qualified='p#'+n,
                        solved=dict(x=0., z=0., footprint_orientation=
                                    '16 m along x by 4 m along z'))
                   for n in ('early-dock', 'sandbox-harbour')]
        with self.assertRaisesRegex(SystemExit, 'canonical'):
            installation_footprints({'dockshore': (0., 0.),
                                     'portalhub': (10., 0.)}, placements=records)


class PhysicalSourceConsumerTests(unittest.TestCase):
    def test_rooted_apron_not_foreign_sandbar_supplies_served_coast(self):
        import contextlib
        import io
        import route

        half = 800.5
        axis = -half + .5 + np.arange(1601)
        x, z = np.meshgrid(axis, axis)
        heights = np.where(z <= 0, S.DECK_Y,
                           np.maximum(25., S.DECK_Y-z*.8)).astype(np.float32)
        heights[(abs(x) <= 1) & (abs(z-20) <= 1)] = S.DECK_Y
        field = S.W.Field('shore-trunk-primary', 0, 0, half, 1, 1601,
                          heights, np.ones(heights.shape, dtype=np.uint8))
        complete = CompleteLand(field, 200)
        footprint = dict(owner='fixture#station', xz=[0., 20.],
                         width_m=64., depth_m=64., network_root_xz=[0., -40.])
        nodes = {'stathub': dict(xz=(0., 20.), patch='trunk-primary',
                                island='spawn', source_footprint=footprint)}
        def graph(field, cls):
            corridor = route.Corridor(field, cell_m=4., grade_max=route.GRADE[cls])
            return S.geography.RoadGraph(field, cls=cls,
                forbid=np.zeros((corridor.m, corridor.m), dtype=bool))
        requirement = next(r for r in S.REQUIREMENTS if r['id'] == 'dock-stationhub')
        with contextlib.redirect_stdout(io.StringIO()):
            site = S.solve_site(requirement, nodes,
                {field.id: field, 'trunk-primary': field}, complete=complete,
                graph_factory=graph)
        self.assertEqual(site['rendered_component'], complete.at(0., -40.))
        self.assertNotEqual(site['rendered_component'], complete.at(0., 20.))
        self.assertTrue(site['structural']['ok'])


class RootedExteriorApproachTests(unittest.TestCase):
    def setUp(self):
        from shore_sources import footprint_contact, footprint_approaches

        n = 257
        axis = np.arange(n, dtype=float) - n // 2
        x, z = np.meshgrid(axis, axis)
        heights = np.where(z <= 0, S.DECK_Y,
                           np.maximum(25., S.DECK_Y - z * .8)).astype(np.float32)
        heights[(abs(x) <= 1) & (abs(z - 20) <= 1)] = S.DECK_Y
        field = S.W.Field('rooted-exterior', 0., 0., n / 2, 1., n, heights,
                          np.ones(heights.shape, dtype=np.uint8))
        self.complete = CompleteLand(field, 40)
        self.footprint = dict(owner='fixture#station', xz=[0., 20.],
                              width_m=64., depth_m=64.,
                              network_root_xz=[0., -40.])
        self.contact = footprint_contact(self.complete, self.footprint)
        self.node = dict(xz=(0., 20.), source_footprint=self.footprint,
                         level_y=S.DECK_Y + 20.)
        native = S.F.Field(field.id, field.cx, field.cz, field.half, field.step,
                           field.n, field.heights, field.biomes)
        corr = S.route.Corridor(native, cell_m=4., grade_max=S.route.GRADE['spur'])
        self.graph = S.geography.RoadGraph(
            native, cls='spur', forbid=np.zeros((corr.m, corr.m), dtype=bool))
        self.original_ok = self.graph.ok.copy()
        self.masked, self.approaches = footprint_approaches(
            self.graph, self.footprint, self.contact, 4., complete=self.complete)
        self.surface = S.GeneratedSurface({field.id: field})
        self.root = (48., 0.)

    def test_rooted_writer_contact_selects_real_exterior_not_semantic_rock_or_root(self):
        import arrival_geometry

        self.assertNotEqual(self.complete.at(*self.node['xz']), self.contact['component'])
        self.assertTrue(self.approaches)
        corr = self.masked.corr
        level = S.route.footprint_half_m(4.)
        pad = dict(x=0., z=20., hw=32., hd=32.)
        ring = set(arrival_geometry.arrival_ring_cells(
            self.node['xz'], corr.nearest_allowed(*self.node['xz']),
            corr, [pad], level))
        for approach in self.approaches:
            ex, ez = approach['handoff_xz']
            self.assertIn(corr.cell_of(ex, ez), ring)
            self.assertTrue(abs(ex) > 32. + level or abs(ez - 20.) > 32. + level)
            self.assertEqual(self.complete.at(*approach['contact_xz']),
                             self.contact['component'])
            self.assertEqual(self.complete.at(*approach['rooted_sample_xz']),
                             self.contact['component'])
        preview = S.road_preview(self.masked, self.surface, self.node,
            self.root, 0., 4.,
            source_approaches=self.approaches)
        self.assertIsNotNone(preview)
        self.assertEqual(preview['nodes'][0], preview['source_approach']['handoff_xz'])
        self.assertNotEqual(preview['nodes'][0], list(self.node['xz']))
        self.assertNotEqual(preview['nodes'][0], self.footprint['network_root_xz'])
        self.assertEqual(preview['nodes'][-1], list(self.root))
        self.assertIsNone(preview['source_level_y'])
        self.assertEqual(preview['source_approach']['owner'], self.contact['owner'])
        self.assertEqual(self.node['xz'], (0., 20.))
        for x, z in preview['nodes']:
            self.assertTrue(abs(x) > 32. + level or abs(z - 20.) > 32. + level)
        np.testing.assert_array_equal(self.graph.ok, self.original_ok)
        self.assertIsNone(self.graph.corr.forbid)

    def test_isolated_nearest_exterior_keeps_uncapped_alternate_contacts(self):
        self.assertGreater(len(self.approaches), 1)
        first = self.approaches[0]
        i, j = self.masked.corr.cell_of(*first['handoff_xz'])
        # An actual forbidden-cell pocket, not a mocked route verdict. The
        # first handoff itself stays allowed but no inland road can reach it.
        self.masked.ok[i - 1:i + 2, j - 1:j + 2] = False
        self.masked.ok[i, j] = True
        self.masked.corr.forbid = ~self.masked.ok
        self.assertIsNone(S.road_preview(
            self.masked, self.surface, self.node, self.root, 0., 4.,
            source_approaches=[first]))
        preview = S.road_preview(
            self.masked, self.surface, self.node, self.root, 0., 4.,
            source_approaches=self.approaches)
        self.assertIsNotNone(preview)
        self.assertNotEqual(preview['source_approach']['handoff_xz'], first['handoff_xz'])


if __name__ == '__main__':
    unittest.main()
