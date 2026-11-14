"""Named road obligations require their own saved, ordered walking proof."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from accept.sections import roads as A
import ribbon as R


def fixture():
    identities = [
        dict(id='approach-opaque-17', **{'from': 'named-town', 'to': 'bank-east'},
             cls='trunk', width_m=6.),
        dict(id='approach-opaque-23', **{'from': 'bank-west', 'to': 'named-harbour'},
             cls='trunk', width_m=6.)]
    physical = {str(i): dict(seg=r['id'], identity=dict(r), identity_proven=True,
                            half_width_m=3., nodes=[(i * 4., 0.), (i * 4. + 1., 0.)])
                for i, r in enumerate(identities)}
    parts = [dict(kind='road', id=identities[0]['id'],
                  **{'from': 'named-town', 'to': 'bank-east'}, reverse=False),
             dict(kind='declared_crossing', id='S1',
                  **{'from': 'bank-east', 'to': 'bank-west'}),
             dict(kind='road', id=identities[1]['id'],
                  **{'from': 'bank-west', 'to': 'named-harbour'}, reverse=False)]
    forward = dict(id='T10', **{'from': 'named-town', 'to': 'named-harbour'},
                   cls='trunk', width_m=6., pri=1, status='PLANNED', parts=parts)
    reverse_parts = []
    for p in reversed(parts):
        q = dict(p, **{'from': p['to'], 'to': p['from']})
        if p['kind'] == 'road':
            q['reverse'] = True
        reverse_parts.append(q)
    backward = dict(forward, id='T11', **{'from': 'named-harbour', 'to': 'named-town'},
                    parts=reverse_parts)
    authored = [{k: r[k] for k in ('id', 'from', 'to', 'cls', 'width_m', 'pri')}
                for r in (forward, backward)]
    doc = dict(segments=identities, road_requirements=[forward, backward],
               road_requirement_scope=dict(complete=True,
                   basis='complete_unfiltered_authored_population', only=[], pri=[],
                   authored_ids=['T10', 'T11'], selected_ids=['T10', 'T11'],
                   excluded_inputs=[], no_road={}))
    keys = ['0|0', '0|1', 'x0|0', 'x0|1', '1|0', '1|1']
    adj = {k: [] for k in keys}
    for a, b in zip(keys, keys[1:]):
        adj[a].append(b)
        adj[b].append(a)
    nodes = {('road', identities[0]['id']): (keys[0], keys[1], set(keys[:2])),
             ('declared_crossing', 'S1'): (keys[2], keys[3], set(keys[2:4])),
             ('road', identities[1]['id']): (keys[4], keys[5], set(keys[4:]))}
    decks = [dict(id='S1', **{'from': 'bank-east', 'to': 'bank-west'},
                  status='declared', owner='Crossings',
                  declaration_status='declared', width_m=6.)]
    spans = [dict(crossing='S1', width_contract={'pass': True}, **{'pass': True})]
    return doc, authored, physical, adj, nodes, decks, spans


class NamedRoadAcceptanceTests(unittest.TestCase):
    def prove(self, data, *, surface_edges=None, node_heights=None):
        if node_heights is None:
            node_heights = {key: {31.} for key in data[3]}
        if surface_edges is None:
            positions = {key: (float(i), 0.) for i, key in enumerate(data[3])}
            surfaces = {key: [(31., None)] for key in data[3]}
            captured = {(a, b): [(positions[a], surfaces[a],
                                  positions[b], surfaces[b], [])]
                        for a, neighbours in data[3].items() for b in neighbours}
            surface_edges = A.measured_surface_edges(captured, positions, surfaces)
        return A.named_road_requirements(
            *data, surface_edges=surface_edges, node_heights=node_heights)

    def test_reverse_roles_share_physical_parts_but_keep_independent_proofs(self):
        report = self.prove(fixture())
        self.assertTrue(report['pass'], report)
        self.assertEqual([r['id'] for r in report['requirements']], ['T10', 'T11'])
        walks = [r['walking_nodes'] for r in report['requirements']]
        self.assertEqual(walks[0], walks[1][::-1])
        self.assertEqual(walks[0], ['0|0', '0|1', 'x0|0', 'x0|1', '1|0', '1|1'])

    def test_omitted_requirement_does_not_shrink_source_denominator(self):
        data = fixture()
        data[0]['road_requirements'].pop()
        result = self.prove(data)
        self.assertFalse(result['pass'])
        self.assertIn('missing requirement T11', result['source_issues'])

    def test_scope_cannot_echo_an_omitted_authored_role(self):
        data = fixture()
        data[0]['road_requirements'].pop()
        data[0]['road_requirement_scope'].update(
            authored_ids=['T10'], selected_ids=['T10'])
        self.assertFalse(self.prove(data)['pass'])

    def test_partial_source_population_is_hard_unresolved(self):
        for change in (dict(complete=False), dict(only=['T10']), dict(pri=[1])):
            data = fixture()
            data[0]['road_requirement_scope'].update(change)
            self.assertFalse(self.prove(data)['pass'])

    def test_missing_physical_part_is_not_proved_by_its_plan_row(self):
        data = fixture()
        del data[2]['1']
        self.assertFalse(self.prove(data)['pass'])
        data = fixture()
        data[0]['segments'].pop()
        self.assertFalse(self.prove(data)['pass'])

    def test_voyage_only_connection_cannot_satisfy_a_road(self):
        data = fixture()
        data[3]['x0|0'].remove('x0|1')
        data[3]['x0|1'].remove('x0|0')
        # An accepted voyage is intentionally absent from the walking graph.
        travel_adj = deepcopy(data[3])
        travel_adj['x0|0'].append('x0|1')
        travel_adj['x0|1'].append('x0|0')
        self.assertIn('x0|1', travel_adj['x0|0'])
        self.assertFalse(self.prove(data)['pass'])

    def test_reachability_of_both_ends_on_unlisted_roads_is_not_a_part_proof(self):
        data = fixture()
        data[3]['0|0'].remove('0|1')
        data[3]['0|1'].remove('0|0')
        data[3]['0|0'].append('foreign|0')
        data[3]['foreign|0'] = ['0|0', '0|1']
        data[3]['0|1'].append('foreign|0')
        self.assertFalse(self.prove(data)['pass'])

    def test_missing_walking_handoff_is_not_replaced_by_named_bank_echo(self):
        data = fixture()
        data[3]['0|1'].remove('x0|0')
        data[3]['x0|0'].remove('0|1')
        self.assertFalse(self.prove(data)['pass'])

    def test_narrow_wrong_class_or_mismatched_saved_road_is_refused(self):
        for mutation in ('narrow', 'class', 'endpoint', 'plan'):
            data = fixture()
            road = data[2]['0']
            if mutation == 'narrow':
                road['half_width_m'] = 2.
            elif mutation == 'class':
                road['identity']['cls'] = 'spur'
            elif mutation == 'endpoint':
                road['identity']['from'] = 'some-other-town'
            else:
                data[0]['segments'][0]['to'] = 'unrelated-bank'
            with self.subTest(mutation=mutation):
                self.assertFalse(self.prove(data)['pass'])

    def test_missing_narrow_unmeasured_and_wrong_identity_decks_are_refused(self):
        for mutation in ('missing', 'narrow', 'unmeasured', 'bank'):
            data = fixture()
            if mutation == 'missing':
                data[5].clear()
            elif mutation == 'narrow':
                data[5][0]['width_m'] = 4.
            elif mutation == 'unmeasured':
                data[6][0]['pass'] = False
            else:
                data[5][0]['from'] = 'invented-bank'
            with self.subTest(mutation=mutation):
                self.assertFalse(self.prove(data)['pass'])

    def test_duplicate_foreign_and_mismatched_requirements_are_refused(self):
        for mutation in ('duplicate', 'foreign', 'mismatch', 'unordered'):
            data = fixture()
            if mutation == 'duplicate':
                data[0]['road_requirements'].append(deepcopy(data[0]['road_requirements'][0]))
            elif mutation == 'foreign':
                data[0]['road_requirements'][0]['id'] = 'foreign'
            elif mutation == 'mismatch':
                data[0]['road_requirements'][0]['width_m'] = 4.
            else:
                data[0]['road_requirements'][0]['parts'].reverse()
            with self.subTest(mutation=mutation):
                self.assertFalse(self.prove(data)['pass'])

    def test_unread_endpoint_is_not_moved_to_first_read_interior_station(self):
        data = fixture()
        key = ('road', 'approach-opaque-17')
        _, end, allowed = data[4][key]
        data[4][key] = (None, end, allowed)
        self.assertFalse(self.prove(data)['pass'])

    def test_legacy_id_tokens_are_not_endpoint_authority(self):
        data = fixture()
        data[2]['0'].update(identity=None, identity_proven=False)
        self.assertFalse(self.prove(data)['pass'])

    def test_excluded_inputs_and_no_road_remain_explicit_evidence(self):
        doc, authored, *_ = fixture()
        self.assertFalse(A.named_road_plan_status(
            doc, authored, {'removed': 'foreign measured nodes'}, {})['pass'])
        self.assertFalse(A.named_road_plan_status(
            doc, authored, {}, {'no-road-town': 'measured portal only'})['pass'])

    def test_pairwise_edges_cannot_switch_between_disconnected_surface_layers(self):
        parts = [('a', 'c', {'a', 'b', 'c'})]
        adj = {'a': ['b'], 'b': ['a', 'c'], 'c': ['b']}
        pos = {'a': (0., 0.), 'b': (1., 0.), 'c': (2., 0.)}
        surfaces = {'a': [(31., None)], 'b': [(31., None), (35., None)],
                    'c': [(35., None)]}
        captured = {('a', 'b'): [(pos['a'], surfaces['a'], pos['b'], surfaces['b'], [])],
                    ('b', 'c'): [(pos['b'], surfaces['b'], pos['c'], surfaces['c'], [])]}
        self.assertTrue(A.edge_ok(surfaces['a'], surfaces['b'], 1.))
        self.assertTrue(A.edge_ok(surfaces['b'], surfaces['c'], 1.))
        measured = A.measured_surface_edges(captured, pos, surfaces)
        self.assertIsNone(A.continuous_road_witness(
            parts, adj, measured, {k: A.receipt_heights(v) for k, v in surfaces.items()}))

    def test_junction_receipt_reads_wet_unread_and_diagonal_interior(self):
        for mutation in ('wet', 'unread', 'corner-wall', 'obstructed', 'clear'):
            diagonal = mutation == 'corner-wall'
            pos = {'a': (0., 0.), 'b': (1., 1.) if diagonal else (3., 0.)}
            surfaces = {'a': [(31., None)], 'b': [(31., None)]}
            captured = {}
            def read_ground(points):
                if mutation == 'unread':
                    return {}
                return {label: (35. if diagonal else 29. if mutation == 'wet' else 31.)
                        for label, _x, _z in points}
            A.near_miss_bridges(
                pos, surfaces, {}, {'a': 'left', 'b': 'right'}, read_ground,
                lambda _x, _z, y: [(y, None)],
                candidate_edges=[(1.414 if diagonal else 3., 'a', 'b')],
                captured_walks=captured)
            measured = A.measured_surface_edges(
                captured, pos, surfaces,
                lambda point, _y, _other, _a, _b:
                    mutation != 'obstructed' or point != (1., 0.))
            result = A.continuous_road_witness(
                [('a', 'b', {'a', 'b'})], {'a': ['b'], 'b': ['a']},
                measured, {'a': {31.}, 'b': {31.}})
            with self.subTest(mutation=mutation):
                self.assertEqual(result is not None, mutation == 'clear')

    def test_saved_deck_width_requires_actual_tight_full_width_at_every_wet_metre(self):
        crossing = dict(a=(0., 0.), b=(2., 0.), width_m=6.,
                        samples=[(0., 0.), (1., 0.), (2., 0.)])
        for mutation in ('clear', 'narrow', 'hole', 'bounds', 'unknown', 'wrong-layer'):
            half = 1. if mutation in ('narrow', 'wrong-layer') else 3.
            kind = 'mesh' if mutation == 'bounds' else 'box'
            parts = [['b', -1., -.13, -half, 3., 0., half, kind]]
            if mutation == 'hole':
                parts = [['b', -.1, -.13, -3., .1, 0., 3., 'box']]
            floor = A.I.Solid('floor', (0., 31., 0.), (0., 0., 0., 1.),
                              parts, mutation != 'unknown')
            solids = [floor]
            if mutation == 'wrong-layer':
                solids.append(A.I.Solid(
                    'roof', (0., 35., 0.), (0., 0., 0., 1.),
                    [['b', -1., -.13, -3., 3., 0., 3., 'box']], True))
            width = A.saved_deck_width(
                crossing, [29., 29., 29.], A.I.Grid(solids),
                [[(31., floor)] for _ in crossing['samples']])
            with self.subTest(mutation=mutation):
                self.assertEqual(width['pass'], mutation == 'clear', width)

    def test_plan_status_cannot_name_a_missing_or_foreign_declared_deck(self):
        data = fixture()
        for declarations in ([], [
            dict(crossing_id='S1', owner='Elsewhere', status='declared',
                 bank_a={'node': 'bank-east'}, bank_b={'node': 'bank-west'})]):
            status = A.named_road_plan_status(
                data[0], data[1], crossing_doc={'declared': declarations})
            self.assertFalse(status['pass'])

    def test_construction_enumerates_only_real_physical_segments(self):
        import buildstage
        doc, *_ = fixture()
        doc['road_requirements'][0].update(status='REFUSED', parts=[], why='native refusal')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'segments.yaml'
            path.write_text(yaml.safe_dump(doc))
            with patch.object(buildstage.worldseed, 'segments_path', return_value=path):
                units = buildstage._segment_units(SimpleNamespace(seed='fixture'))
        self.assertEqual(units, ['approach-opaque-17', 'approach-opaque-23'])
        self.assertNotIn('T10', units)
        self.assertNotIn('T11', units)


class PhysicalRoadIdentityTests(unittest.TestCase):
    def record(self):
        segment = dict(id='approach-opaque-17', **{'from': 'named-town', 'to': 'bank-east'},
                       cls='trunk', width_m=6.)
        return dict(op='terrain_write', meta=R.road_identity_meta(segment),
                    params=dict(role='road_segment', name='road_approach_opaque_17',
                                profile=dict(half_width_m=3., shoulder_m=1.,
                                             nodes=[[0., 0., 31.], [1., 0., 31.]])))

    def load(self, record):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.jsonl'
            path.write_text(json.dumps(record) + '\n')
            return next(iter(A.built_segments(path).values()))

    def test_new_write_maps_opaque_id_using_real_endpoint_metadata(self):
        road = self.load(self.record())
        self.assertTrue(road['identity_proven'])
        self.assertEqual((road['seg'], road['from'], road['to']),
                         ('approach-opaque-17', 'named-town', 'bank-east'))

    def test_legacy_and_inconsistent_identity_remain_explicitly_unproven(self):
        for mutation in ('legacy', 'width', 'unit'):
            rec = self.record()
            if mutation == 'legacy':
                del rec['meta']['road_identity']
            elif mutation == 'width':
                rec['meta']['road_identity']['width_m'] = 4.
            else:
                rec['meta']['segment'] = 'not-this-physical-road'
            self.assertFalse(self.load(rec)['identity_proven'])

    def test_writer_refuses_missing_physical_endpoint_identity(self):
        with self.assertRaises(KeyError):
            R.road_identity_meta(dict(id='opaque', cls='trunk', width_m=6.))


if __name__ == '__main__':
    unittest.main()
