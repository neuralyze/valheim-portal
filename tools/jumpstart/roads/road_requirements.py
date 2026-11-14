"""Authored road obligations resolved over admitted native ground and real decks."""
from __future__ import annotations

import hashlib
import heapq


def inventory(spec):
    """Retain removed edges as denominator evidence, not invented terrain."""
    edges = [dict(e) for e in spec.EDGES]
    excluded = {}
    for item in spec.FOREIGN_EDGES:
        e = item['edge']
        edges.append(dict(e))
        excluded[e['id']] = spec.NO_ROAD.get(e['id'], 'foreign measured nodes')
    for e in spec.UNREACHABLE_EDGES:
        edges.append(dict(e))
        excluded[e['id']] = spec.NO_ROAD.get(e['id'], 'absent measured nodes')
    ids = [e['id'] for e in edges]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate authored road requirement ID')
    return edges, excluded


def approaches(selected, nodes, crossings, widths):
    """Declare every legal same-island endpoint/bank alternative before anchors.

    Physical pair/class identity is independent of authored direction. No geometry
    here admits a line: every returned edge must pass the ordinary native router.
    """
    banks = {c[s]['node'] for c in crossings for s in ('bank_a', 'bank_b')
             if c.get(s, {}).get('node') in nodes}
    pairs = {}
    for e in selected:
        if nodes[e['a']]['island'] == nodes[e['b']]['island']:
            continue
        terminals = banks | {e['a'], e['b']}
        for a in sorted(terminals):
            for b in sorted(terminals):
                if a >= b or nodes[a]['island'] != nodes[b]['island']:
                    continue
                if a not in banks and b not in banks:
                    continue
                width = float(e.get('width_m') or widths[e['cls']])
                key = (a, b, e['cls'], str(width))
                digest = hashlib.sha256('\0'.join(key).encode()).hexdigest()[:20]
                row = dict(id='approach-' + digest, a=a, b=b, cls=e['cls'],
                           pri=e['pri'], width_m=width, requirement_approach=True,
                           patch=nodes[a]['patch'])
                if key not in pairs or row['pri'] < pairs[key]['pri']:
                    pairs[key] = row
    return [pairs[k] for k in sorted(pairs)]


def authored_population(spec):
    """Consumer normalization of the complete, unfiltered source inventory."""
    from crossing_contract import WIDTH_M
    edges, _excluded = inventory(spec)
    return [dict(id=e['id'], **{'from': e['a'], 'to': e['b']},
                 cls=e['cls'], width_m=float(e.get('width_m') or WIDTH_M[e['cls']]), pri=e['pri'])
            for e in edges]


def class_adequate(actual, required):
    """Existing native grade classes, in increasing permitted steepness."""
    from route import GRADE
    return actual in GRADE and required in GRADE and GRADE[actual] <= GRADE[required]


def resolve(authored, selected_ids, excluded, segments, crossings, widths,
            served_nodes, deck_width, *, only=(), pri=(), no_road=None,
            failures=(), refusal_of=None):
    """Resolve ordered directional parts AFTER final joint/water closure.

    PLANNED is necessary plan resolution, never construction/save admission.
    Widths come from the physical road and actual declared deck handoffs.
    """
    selected_ids = set(selected_ids)
    failure_by_id = {r['id']: r.get('why', r.get('error', 'native refusal'))
                     for r in failures}
    physical = []
    handoffs = {}
    for seg in segments:
        refusal = refusal_of(seg) if refusal_of is not None else None
        if refusal is not None:
            failure_by_id[seg['id']] = refusal
            continue
        if (seg.get('residual_infeasible')
                or seg.get('verdict') == 'REFUSED' or seg.get('status') == 'REFUSED'
                or seg.get('terminal_profile_refusal', {}).get('verdict') == 'REFUSED'):
            failure_by_id[seg['id']] = 'native physical/profile refusal'
            continue
        if seg.get('rejected') or seg.get('writer_water', {}).get('final_verdict') == 'REFUSED':
            continue
        if seg.get('junction_datum', {}).get('verdict') == 'REFUSED':
            continue
        if seg.get('census', {}).get('verdict') == 'PROVISIONAL':
            continue
        served = set(served_nodes(seg))
        a, b = seg['from'], seg['to']
        if a not in served or b not in served:
            continue
        width = float(seg.get('width_m', widths[seg['cls']]))
        physical.append((seg, a, b, width))
        for n in (a, b):
            handoffs.setdefault(n, set()).add(width)
    decks = []
    for c in crossings:
        if c.get('status') != 'declared' or c.get('owner') != 'Crossings':
            continue
        a, b = (c[s]['node'] for s in ('bank_a', 'bank_b'))
        width = deck_width({n: handoffs[n] for n in (a, b) if n in handoffs})
        if a not in handoffs or b not in handoffs or width is None:
            continue
        decks.append((c, a, b, float(width)))
    rows = []
    for e in authored:
        row = dict(id=e['id'], **{'from': e['a'], 'to': e['b']},
                   cls=e['cls'], width_m=float(e.get('width_m') or widths[e['cls']]), pri=e['pri'],
                   status='REFUSED', parts=[])
        rows.append(row)
        if e['id'] in excluded:
            row['why'] = excluded[e['id']]
            continue
        if e['id'] not in selected_ids:
            row['why'] = 'unselected by --only/--pri; explicit partial-run debt'
            continue
        adj = {}
        def add(a, b, part, cost):
            adj.setdefault(a, []).append((b, part, cost))
        for seg, a, b, width in physical:
            if width < row['width_m'] or not class_adequate(seg['cls'], e['cls']):
                continue
            new = int(seg['id'].startswith('approach-'))
            for u, v, reverse in ((a, b, False), (b, a, True)):
                add(u, v, dict(kind='road', id=seg['id'], **{'from': u, 'to': v},
                               reverse=reverse), (new, float(seg['length_m'])))
        for c, a, b, width in decks:
            if width < row['width_m']:
                continue
            for u, v in ((a, b), (b, a)):
                add(u, v, dict(kind='declared_crossing', id=c['crossing_id'],
                               **{'from': u, 'to': v}), (0, float(c['span_m'])))
        heap = [(0, 0.0, (), e['a'], [])]
        best = {}
        while heap:
            new, length, tie, node, parts = heapq.heappop(heap)
            score = (new, length, tie)
            if node in best:
                continue
            best[node] = score
            if node == e['b']:
                row.update(status='PLANNED', parts=parts)
                break
            for dest, part, cost in sorted(adj.get(node, []), key=lambda x: (x[0], x[1]['id'])):
                if dest not in best:
                    heapq.heappush(heap, (new + cost[0], length + cost[1],
                                         tie + (part['id'] + ':' + dest,), dest,
                                         parts + [part]))
        if row['status'] == 'REFUSED':
            row['why'] = failure_by_id.get(e['id'],
                'no admitted adequate-width physical ground/deck path; missing, unserved or refused parts')
            row['physical_refusals'] = dict(sorted(failure_by_id.items()))
    scope = dict(complete=not (only or pri), basis='complete_unfiltered_authored_population',
                 only=list(only or ()), pri=list(pri or ()),
                 authored_ids=[e['id'] for e in authored], selected_ids=sorted(selected_ids),
                 excluded_inputs=[dict(id=k, why=v) for k, v in sorted(excluded.items())],
                 no_road=dict(no_road or {}))
    return rows, scope


def complete_approaches(selected, nodes, crossings, widths, *,
                        resolve_current, route, close):
    """Exhaust finite, predeclared candidates against FINAL native admission.

    A reused path can disappear in joint/water closure. Re-resolve only after
    that same closure, route every still-untried alternative for the resulting
    debt, and close the combined native population again. No retry counter or
    initial PLANNED result can hide an untried bank.
    """
    attempted = set()
    while True:
        close()
        rows = resolve_current()
        pending = {r['id'] for r in rows if r['status'] == 'REFUSED'}
        candidates = approaches([e for e in selected if e['id'] in pending],
                                nodes, crossings, widths)
        untried = [e for e in candidates if e['id'] not in attempted]
        if not untried:
            return rows
        for edge in untried:
            attempted.add(edge['id'])
            route(edge)
