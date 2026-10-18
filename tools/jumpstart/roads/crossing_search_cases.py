"""Constructed canonical counterexamples for the physical-query caches.

These functions are parent-run tests, never measured-world evidence.
"""
from __future__ import annotations

import math

import numpy as np

import field as fieldmod
import route
from crossing_search import ApproachTree, SeatingRefusals


def search_parity_cases():
    def field(height=40.0, river=False):
        h = np.full((65,65),height,dtype=np.float32)
        if river:
            h[:,28:32] = 29.9
        return fieldmod.Field('constructed-search',0.0,0.0,32.5,1.0,65,
                              h,np.ones((65,65),dtype=np.uint8))
    def cost(corr,path):
        if path is None:
            return None
        total = 0.0
        for a,b in zip(path,path[1:]):
            ca,cb = corr.cell_of(*a),corr.cell_of(*b)
            edge = corr._step_cost(ca,cb,math.dist(a,b))
            if edge is None:
                raise AssertionError('tree returned an edge the real Corridor refuses')
            total += edge
        return total
    rows = []
    for name,river,land_only,wall in (
        ('dry_diagonal_cost_parity',False,True,False),
        ('protected_full_column_refuses',False,True,True),
        ('water_full_column_refuses_land_only',True,True,False),
        ('water_entry_and_exit_cost_parity',True,False,False)):
        corr = route.Corridor(field(30.6 if river else 40.0,river),cell_m=4.0)
        corr.forbid = np.zeros((corr.m,corr.m),dtype=bool)
        corr.forbid_all_water = land_only
        if wall:
            corr.forbid[:,7] = True
        start,goal = ((1,1),(14,14)) if not river and not wall else ((7,1),(7,14))
        native = corr._astar(start,goal)
        tree = ApproachTree(corr,start)
        cached = tree.path_to(goal)
        a,b = cost(corr,native),cost(corr,cached)
        same = a is None and b is None or a is not None and b is not None and abs(a-b)<1e-9
        rows.append(dict(case=name,native_cost=a,shared_tree_cost=b,
                         matches_expected=bool(same)
                            and (cached is not None) == (not (wall or river and land_only))))
    # Target-specific feedback mutates its own mask. The previous tree retains
    # its original predicate and must NOT inherit this new protected cell.
    corr = route.Corridor(field(),cell_m=4.0)
    corr.forbid = np.zeros((corr.m,corr.m),dtype=bool)
    corr.forbid_all_water = True
    start,goal = (7,1),(7,14)
    base = ApproachTree(corr,start)
    original = base.path_to(goal)
    corr.forbid[7,7] = True
    changed = ApproachTree(corr,start)
    alternative = changed.path_to(goal)
    native = corr._astar(start,goal)
    rows.append(dict(case='changed_mask_has_independent_tree_and_later_route_survives',
        base_cost=cost(base.corr,original),changed_cost=cost(corr,alternative),
        native_changed_cost=cost(corr,native),
        matches_expected=alternative is not None and original == base.path_to(goal)
            and abs(cost(corr,alternative)-cost(corr,native))<1e-9
            and cost(corr,alternative)>cost(base.corr,original)))
    return rows


def seating_dependency_cases():
    import boardwalk as bw
    span = dict(crossing_id='constructed-cache',segment='constructed-cache',
        bank_a=dict(xz=[0.0,0.0],ground_y=30.6),
        bank_b=dict(xz=[0.0,20.0],ground_y=30.6),span_m=20.0,
        deck_width_m=6.0,bank_level_run_m=6.0,single_H_both_banks=True,
        deck_y_REQUIRED=30.6,legal_deck_y_interval=[30.6,30.6],
        water_surface_y=30.0,terminal_banks=[])
    def clear(x,z):
        return 28.0 if 1.0<z<19.0 else 30.6
    def failing(x,z):
        if 3.0<=z<=5.0:
            return 31.042
        if 1.0<z<3.0:
            return 27.832
        return clear(x,z)
    memo = SeatingRefusals(span,30.6)
    refusal = memo.probe(failing)
    same = memo.blocked(failing)
    changed = memo.blocked(clear)
    survived = bw.prepare_geometry(span,bw.asm.Bed.applied(clear,
        'CONSTRUCTED alternative planned surface, no world claim'))
    rows = [dict(case='protected_wet_ceiling_refusal_is_reused_exactly',
                 matches_expected=refusal is not None and same is refusal),
            dict(case='changed_surface_invalidates_failure_and_later_plan_survives',
                 matches_expected=changed is None and not any(survived['guards'].values()))]
    old_id = span['crossing_id']
    span['crossing_id'] = 'different-span-input'
    rows.append(dict(case='changed_nonterrain_span_argument_invalidates_certificate',
                     matches_expected=memo.blocked(failing) is None))
    span['crossing_id'] = old_id
    old_step = bw.LAND_TILE_STEP_M
    try:
        bw.LAND_TILE_STEP_M = old_step+0.01
        changed_policy = memo.blocked(failing)
        still_refused = memo.probe(failing)
        rows.append(dict(case='changed_policy_invalidates_but_preserves_real_wet_refusal',
            matches_expected=changed_policy is None and still_refused is not None))
    finally:
        bw.LAND_TILE_STEP_M = old_step
    # Actual canonical counterexample: SAME TERRAIN, changed route shape.
    # The land-seating callee accepts both. Its cache must never contain a
    # full prepare/route-guard failure, since those failures consume routes.
    route_span = {**span,'deck_y_REQUIRED':32.5,'legal_deck_y_interval':[32.5,32.5]}
    level = lambda x,z: 32.5
    road_memo = SeatingRefusals(route_span,32.5)
    seated = road_memo.probe(level)
    bed = bw.asm.Bed.applied(level,'CONSTRUCTED protected-support scene')
    support = bw.asm.Placed('wood_pole',0.0,33.0,10.0,0.0,'constructed/bent/pile')
    crossing = (dict(seg='crosses-protected-support',half_width_m=2.0,
                     nodes=[[-10.0,10.0],[10.0,10.0]]),)
    clear_route = (dict(seg='misses-protected-support',half_width_m=2.0,
                        nodes=[[-10.0,20.0],[10.0,20.0]]),)
    refused = bw.route_carriageway_guard(route_span,[support],bed,crossing)
    passed = bw.route_carriageway_guard(route_span,[support],bed,clear_route)
    rows.append(dict(case='route_shape_changes_real_guard_refusal_without_terrain_change',
        protected_refusal=refused,later_route_refusal=passed,
        matches_expected=seated is None and bool(refused) and not passed
            and not road_memo.failures))
    rows.append(short_final_bay_policy_case())
    return rows


def coastal_handoff_cases():
    """An unauthored low-shore goal may be graded ONLY if real writer passes."""
    from geography import RoadGraph
    from crossing_physical import PhysicalFields
    zz,_xx = np.mgrid[-128:129,-128:129]
    heights = np.where((zz>0)&(zz<20),28.0,30.6).astype(np.float32)
    field = fieldmod.Field('constructed-coastal-handoff',0.0,0.0,128.5,1.0,257,
                          heights,np.ones((257,257),dtype=np.uint8))
    graph = RoadGraph(field,forbid=np.zeros((64,64),dtype=bool))
    physical = PhysicalFields({field.id:field},
        {'constructed':True,'surface':'heightmap'},keepout_discs=())
    a,b = {'xz':[0.0,-2.0]},{'xz':[0.0,22.0]}
    near = physical.routed_approach(graph,a,(0.0,-32.0),30.6)
    far = physical.routed_approach(graph,b,(0.0,52.0),30.6)
    proof = physical._candidate_on_paths({'id':'constructed-low-shore'},
        a,b,30.6,near,far) if near is not None and far is not None else {
            'ok':False,'stage':'canonical_router'}
    return [dict(case='unauthored_low_shore_handoff_uses_legal_real_writer',
                 expected_buildable=True,physical=proof,
                 matches_expected=proof['ok'])]


def short_final_bay_policy_case():
    """A 0.2 m final bay changes actual seating when duplicate policy changes."""
    import boardwalk as bw
    span = dict(crossing_id='constructed-short-final-bay',segment='constructed',
        bank_a=dict(xz=[0.0,0.1],ground_y=30.6),
        bank_b=dict(xz=[0.0,6.3],ground_y=30.6),span_m=6.2,
        deck_width_m=6.0,bank_level_run_m=6.0,single_H_both_banks=True,
        deck_y_REQUIRED=30.6,legal_deck_y_interval=[30.6,30.6],
        water_surface_y=30.0,terminal_banks=[])
    def ground(x,z):
        if 2.5 <= z <= 4.5:
            return 31.042
        return 30.6 if z <= 0.0 or z >= 6.0 or 1.5 < z < 2.5 else 28.0
    bed = bw.asm.Bed.applied(ground,
        'CONSTRUCTED short final bay: high vertices 3/4, wet neighbours 1/5')
    def actual_seating():
        try:
            stations = bw.land_seated_deck_stations(span,bed,30.6,report={})
        except SystemExit as error:
            return dict(status='refused',why=str(error))
        return dict(status='seated',stations=stations)
    gate = bw.asm._gate()
    saved = gate.DUP_TOL_M
    memo = SeatingRefusals(span,30.6)
    try:
        gate.DUP_TOL_M = 0.2
        wide_min = bw.asm.min_bay_m()
        wide_rows = bw.asm._bridge_station_distances(6.2,1)
        wide = actual_seating()
        learned = memo.probe(ground)
        gate.DUP_TOL_M = 0.05
        narrow_min = bw.asm.min_bay_m()
        narrow_rows = bw.asm._bridge_station_distances(6.2,1)
        reused = memo.blocked(ground)
        narrow = actual_seating()
        later = memo.probe(ground)
    finally:
        gate.DUP_TOL_M = saved
    return dict(case='transitive_duplicate_policy_changes_real_short_bay_seating',
        span_m=6.2,last_native_bay_m=0.2,
        wide_min_bay_m=wide_min,wide_station_distances=wide_rows,wide_outcome=wide,
        narrow_min_bay_m=narrow_min,narrow_station_distances=narrow_rows,
        narrow_outcome=narrow,
        matches_expected=wide['status']=='refused' and learned is not None
            and any(abs(t-3.1)<1e-9 for t in wide_rows)
            and reused is None and narrow['status']=='seated' and later is None
            and wide_rows != narrow_rows)


def coverage_exhaustion_cases():
    """Actual multi-resolution graphs distinguish unknowns from hard pin failures."""
    from geography import RoadGraph
    from crossing_physical import PhysicalFields, CoverageError
    coarse = fieldmod.Field('constructed-coarse',0.0,0.0,168.0,16.0,21,
        np.full((21,21),40.0,dtype=np.float32),np.ones((21,21),dtype=np.uint8))
    graph = RoadGraph(coarse,forbid=np.zeros((21,21),dtype=bool))
    rows = []
    for n,expected in ((257,'unverified_coverage'),(513,'refused')):
        fine = fieldmod.Field('constructed-integer',0.0,0.0,n/2.0,1.0,n,
            np.full((n,n),40.0,dtype=np.float32),np.ones((n,n),dtype=np.uint8))
        physical = PhysicalFields({coarse.id:coarse,fine.id:fine},
            {'constructed':True,'surface':'heightmap'},keepout_discs=())
        physical.bind_approach_graphs(graph,graph)
        proof = physical.candidate({'id':'constructed-coverage'},
            {'xz':[0.0,-2.0]},{'xz':[0.0,22.0]},30.6)
        rows.append(dict(case='exhaustion_'+expected,status=proof['status'],
            stage=proof['stage'],coverage=proof['coverage'],
            matches_expected=not proof['ok'] and proof['status']==expected
                and (proof['stage']=='coverage') == (expected=='unverified_coverage')
                and (proof['skipped_optional_total']>0) == (expected=='unverified_coverage')))
    small = fieldmod.Field('constructed-insufficient-required',0.0,0.0,32.5,1.0,65,
        np.full((65,65),40.0,dtype=np.float32),np.ones((65,65),dtype=np.uint8))
    physical = PhysicalFields({small.id:small},
        {'constructed':True,'surface':'heightmap'},keepout_discs=())
    physical.bind_approach_graphs(graph,graph)
    try:
        physical.candidate({'id':'constructed-required-coverage'},
            {'xz':[0.0,-2.0]},{'xz':[0.0,22.0]},30.6)
    except CoverageError as error:
        rows.append(dict(case='required_span_coverage_still_raises',
                         diagnostic=str(error),matches_expected=True))
    else:
        rows.append(dict(case='required_span_coverage_still_raises',matches_expected=False))
    return rows
