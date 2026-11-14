"""Directional obligation proofs over admitted physical records, not saved worlds."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import road_requirements as requirements
from crossing_contract import WIDTH_M
import boardwalk


def edge(id='T10', a='town', b='west', cls='trunk'):
    return dict(id=id, a=a, b=b, cls=cls, pri=1)


def road(id, a, b, cls='trunk', **extra):
    return dict(id=id, **{'from': a, 'to': b}, cls=cls,
                width_m=WIDTH_M[cls], length_m=20.0, **extra)


def crossing(id='S1', a='east-bank', b='west-bank'):
    return dict(crossing_id=id, bank_a=dict(node=a), bank_b=dict(node=b),
                owner='Crossings', status='declared', span_m=12.0)


def served(seg):
    return {seg['from'], seg['to']} - set(seg.get('unserved', []))


def resolve(authored, physical, decks=None, **kw):
    return requirements.resolve(authored, kw.pop('selected', [e['id'] for e in authored]),
        kw.pop('excluded', {}), physical, [crossing()] if decks is None else decks,
        WIDTH_M, served, boardwalk.declared_deck_width, **kw)


class RoadRequirementTests(unittest.TestCase):
    def setUp(self):
        self.parts = [road('approach-east', 'town', 'east-bank'),
                      road('approach-west', 'west-bank', 'west')]

    def test_bridge_direction_and_shared_reverse_roles(self):
        rows, scope = resolve([edge(), edge('T11', 'west', 'town')], self.parts)
        self.assertTrue(scope['complete'])
        self.assertEqual([r['status'] for r in rows], ['PLANNED', 'PLANNED'])
        self.assertEqual([p['id'] for p in rows[0]['parts']],
                         ['approach-east', 'S1', 'approach-west'])
        self.assertEqual([p['id'] for p in rows[1]['parts']],
                         ['approach-west', 'S1', 'approach-east'])
        self.assertEqual(rows[1]['parts'][1]['from'], 'west-bank')
        self.assertTrue(rows[1]['parts'][0]['reverse'])

    def test_missing_deck_bank_and_insufficient_ground_width_refuse(self):
        cases = [(self.parts, []), (self.parts[:1], [crossing()]),
                 ([road('narrow', 'town', 'east-bank', 'spur'), self.parts[1]], [crossing()]),
                 ([dict(self.parts[0], unserved=['east-bank']), self.parts[1]], [crossing()])]
        for physical, decks in cases:
            with self.subTest(physical=physical, decks=decks):
                rows, _ = resolve([edge()], physical, decks)
                self.assertEqual(rows[0]['status'], 'REFUSED')
                self.assertEqual(rows[0]['parts'], [])

    def test_actual_deck_width_is_required_independently(self):
        with patch.object(boardwalk, 'declared_deck_width', return_value=4.0):
            rows, _ = resolve([edge()], self.parts)
        self.assertEqual(rows[0]['status'], 'REFUSED')

    def test_native_or_final_refusals_cannot_become_parts(self):
        for mark in ({'junction_datum': {'verdict': 'REFUSED'}},
                     {'writer_water': {'final_verdict': 'REFUSED'}},
                     {'census': {'verdict': 'PROVISIONAL'}},
                     {'rejected': {'why': 'native clamp refusal'}}):
            rows, _ = resolve([edge()], [dict(self.parts[0], **mark), self.parts[1]])
            self.assertEqual(rows[0]['status'], 'REFUSED')
        rows, _ = resolve([edge()], self.parts[1:],
                         failures=[dict(id='approach-east', why='native clamp refusal')])
        self.assertEqual(rows[0]['physical_refusals']['approach-east'], 'native clamp refusal')

    def test_partial_scope_preserves_excluded_and_unselected_denominator(self):
        authored = [edge(), edge('T11', 'west', 'town'), edge('foreign')]
        rows, scope = resolve(authored, self.parts, selected=['T10'], only=['T10'],
                              excluded={'foreign': 'foreign measured seed'},
                              no_road={'foreign': 'foreign measured seed', 'no-site': 'no road'})
        self.assertFalse(scope['complete'])
        self.assertEqual(scope['authored_ids'], ['T10', 'T11', 'foreign'])
        self.assertEqual([r['status'] for r in rows], ['PLANNED', 'REFUSED', 'REFUSED'])
        self.assertIn('unselected', rows[1]['why'])
        self.assertEqual(rows[2]['why'], 'foreign measured seed')
        self.assertIn('no-site', scope['no_road'])

    def test_every_bridge_alternative_survives_first_bank_failure(self):
        decks = [crossing(), crossing('S2', 'east2', 'west2')]
        physical = [self.parts[1], road('east2-road', 'town', 'east2'),
                    road('west2-road', 'west2', 'west')]
        rows, _ = resolve([edge()], physical, decks)
        self.assertEqual(rows[0]['status'], 'PLANNED')
        self.assertEqual([p['id'] for p in rows[0]['parts']], ['east2-road', 'S2', 'west2-road'])

    def test_existing_physical_path_precedes_shorter_new_approach(self):
        existing = road('existing-east', 'town', 'east-bank')
        existing['length_m'] = 100.0
        rows, _ = resolve([edge()], self.parts + [existing])
        self.assertEqual(rows[0]['parts'][0]['id'], 'existing-east')

    def test_same_island_direct_part_keeps_authored_id(self):
        rows, _ = resolve([edge('local', 'town', 'near')], [road('local', 'town', 'near')], [])
        self.assertEqual(rows[0]['status'], 'PLANNED')
        self.assertEqual(rows[0]['parts'][0]['id'], 'local')

    def test_generated_candidates_cover_both_bridges_and_reverse_roles_share(self):
        nodes = {n: dict(island=isl, patch='measured', xz=[0, 0])
                 for n, isl in [('town', 'east'), ('east-bank', 'east'), ('east2', 'east'),
                                ('west', 'west'), ('west-bank', 'west'), ('west2', 'west')]}
        decks = [crossing(), crossing('S2', 'east2', 'west2')]
        forward = requirements.approaches([edge()], nodes, decks, WIDTH_M)
        both = requirements.approaches([edge(), edge('T11', 'west', 'town')], nodes, decks, WIDTH_M)
        self.assertEqual(forward, both)
        pairs = {frozenset((e['a'], e['b'])) for e in forward}
        self.assertTrue({frozenset(('town', 'east-bank')), frozenset(('town', 'east2'))} <= pairs)
        self.assertTrue(all(e['requirement_approach'] for e in forward))
        self.assertTrue(all(nodes[e['a']]['island'] == nodes[e['b']]['island'] for e in forward))

    def test_unfiltered_inventory_retains_foreign_and_absent_edges(self):
        spec = SimpleNamespace(EDGES=[edge()], FOREIGN_EDGES=[{'edge': edge('foreign')}],
            UNREACHABLE_EDGES=[edge('absent')], NO_ROAD={'foreign': 'other seed', 'absent': 'absent bank'})
        authored, excluded = requirements.inventory(spec)
        self.assertEqual([e['id'] for e in authored], ['T10', 'foreign', 'absent'])
        self.assertEqual(set(excluded), {'foreign', 'absent'})
        self.assertEqual(requirements.authored_population(spec)[0]['width_m'], 6.0)

    def test_authored_width_override_survives_inventory_and_resolution(self):
        authored = dict(edge(), width_m=8.0)
        spec = SimpleNamespace(EDGES=[authored], FOREIGN_EDGES=[],
                               UNREACHABLE_EDGES=[], NO_ROAD={})
        self.assertEqual(requirements.authored_population(spec)[0]['width_m'], 8.0)
        rows, _ = resolve([authored], self.parts)
        self.assertEqual(rows[0]['width_m'], 8.0)
        self.assertEqual(rows[0]['status'], 'REFUSED')
        nodes = {n: dict(island=isl, patch='measured', xz=[0, 0])
                 for n, isl in [('town', 'east'), ('east-bank', 'east'),
                                ('west', 'west'), ('west-bank', 'west')]}
        candidates = requirements.approaches([authored], nodes, [crossing()], WIDTH_M)
        self.assertTrue(candidates)
        self.assertTrue(all(e['width_m'] == 8.0 for e in candidates))

    def test_anchor_uses_declared_widest_generated_approach(self):
        import network
        nodes = {'town': dict(patch='measured', xz=[0, 0]),
                 'bank': dict(patch='measured', xz=[10, 0])}
        edges = [dict(edge('ordinary', 'town', 'bank'), width_m=6.0),
                 dict(edge('approach-wide', 'town', 'bank'), width_m=8.0)]
        corridor = SimpleNamespace(cell_world=lambda i, j: (float(i), float(j)))
        with patch.object(network, 'arrival_start', return_value=((1, 2), {})) as arrival:
            anchors = network.node_anchors(edges, nodes, lambda p, c: corridor,
                                           lambda p: object())
        self.assertEqual(set(anchors), {'town', 'bank'})
        self.assertTrue(all(call.args[3] == 8.0 for call in arrival.call_args_list))

    def test_generated_approach_native_constructor_refusal_is_not_bypassed(self):
        import network
        generated = dict(edge('approach-real', 'town', 'east-bank'), requirement_approach=True)
        with patch.dict(network.specmod.NODES, {'town': {}, 'east-bank': {}}, clear=True), \
             patch.object(network, '_build_with_chord_clearance', side_effect=ValueError('native clamp refusal')) as build:
            with self.assertRaisesRegex(ValueError, 'native clamp refusal'):
                network.build_segment(None, generated, None, [], census=None)
        self.assertTrue(build.call_args.args[4])

    def test_final_native_loss_of_reused_s1_path_routes_s2_alternatives(self):
        authored = [edge(), edge('T11', 'west', 'town')]
        nodes = {n: dict(island=isl, patch='measured', xz=[0, 0])
                 for n, isl in [('town', 'east'), ('east-bank', 'east'), ('east2', 'east'),
                                ('west', 'west'), ('west-bank', 'west'), ('west2', 'west')]}
        decks = [crossing(), crossing('S2', 'east2', 'west2')]
        physical = list(self.parts)
        self.assertEqual(resolve(authored, physical, decks)[0][0]['status'], 'PLANNED')
        routed = []
        closed = []
        failures = []

        def close():
            closed.append([s['id'] for s in physical])
            if len(closed) == 1:
                # The reused S1 approach disappears in final native closure.
                physical[:] = [s for s in physical if s['id'] != 'approach-east']

        def native_route(candidate):
            routed.append(candidate['id'])
            pair = frozenset((candidate['a'], candidate['b']))
            if pair in {frozenset(('town', 'east2')), frozenset(('west2', 'west'))}:
                physical.append(road(candidate['id'], candidate['a'], candidate['b']))
            else:
                failures.append(dict(id=candidate['id'], why='native approach refusal'))

        rows = requirements.complete_approaches(
            authored, nodes, decks, WIDTH_M, route=native_route, close=close,
            resolve_current=lambda: resolve(authored, physical, decks, failures=failures)[0])
        self.assertEqual([r['status'] for r in rows], ['PLANNED', 'PLANNED'])
        self.assertEqual([p['id'] for p in rows[0]['parts'] if p['kind'] == 'declared_crossing'], ['S2'])
        self.assertEqual([p['id'] for p in rows[1]['parts'] if p['kind'] == 'declared_crossing'], ['S2'])
        candidates = requirements.approaches(authored, nodes, decks, WIDTH_M)
        self.assertCountEqual(routed, [e['id'] for e in candidates])
        self.assertEqual(len(routed), len(set(routed)))
        self.assertEqual(len(closed), 2)

    def test_final_native_failure_exhausts_candidates_without_duplicate_routes(self):
        authored = [edge()]
        nodes = {n: dict(island=isl, patch='measured', xz=[0, 0])
                 for n, isl in [('town', 'east'), ('east-bank', 'east'),
                                ('west', 'west'), ('west-bank', 'west')]}
        routed = []
        rows = requirements.complete_approaches(
            authored, nodes, [crossing()], WIDTH_M, route=lambda e: routed.append(e['id']),
            close=lambda: None, resolve_current=lambda: resolve(authored, [])[0])
        self.assertEqual(rows[0]['status'], 'REFUSED')
        self.assertTrue(routed)
        self.assertEqual(len(routed), len(set(routed)))

    def test_native_requirement_gate_delegates_to_all_plan_discriminators(self):
        import network
        physical = [dict(seg, crossings=[], writer_water={'verdict': 'CLEAR'})
                    for seg in [road('east', 'town', 'east-bank'), self.parts[1]]]
        native_marks = [
            {'residual_infeasible': [[2, 4]]},
            {'verdict': 'REFUSED'},
            {'terminal_profile_refusal': {'verdict': 'REFUSED'}},
            {'profile_constraints': {'verdict': 'REFUSED', 'bindings': [
                dict(station=2, lower_y=31.0, upper_y=30.0, profile_y=30.5)]}},
            {'self_adjacency': {'verdict': 'REFUSED'}},
            {'census': {'verdict': 'UNCENSUSED'}},
            {'junction_datum': {'verdict': 'REFUSED'}},
            {'write_gate': {'firm_violations': [
                dict(centreline_dist_m=1.0, name='native-poi', xz=[0, 0],
                     required_m=25.0, short_by_m=24.0)]}},
            {'writer_water': {'verdict': 'REFUSED', 'steps': 1, 'drained': 0,
                              'worst_m': 0.9, 'worst_at': [0, 0]}},
        ]
        for mark in native_marks:
            with self.subTest(mark=mark), \
                 patch.object(network, 'terminal_landing_rejection', return_value=None), \
                 patch.object(network, 'bridge_approach_rejection', return_value=None), \
                 patch.object(network, 'write_gate', side_effect=lambda seg, *a:
                              seg.get('write_gate', {'firm_violations': []})):
                native = {**physical[0], **mark}
                rows, _ = resolve([edge()], [native, physical[1]],
                    refusal_of=lambda seg: network.requirement_refusal(
                        dict(seg, crossings=[]), [], 'constructed', fld=None))
                self.assertEqual(rows[0]['status'], 'REFUSED')
                self.assertIn('east', rows[0]['physical_refusals'])


if __name__ == '__main__':
    unittest.main()
