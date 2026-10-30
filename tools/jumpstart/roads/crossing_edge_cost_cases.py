"""Parent-run exact-route and native-work proofs; not measured-world evidence."""
from __future__ import annotations

import copy
import math
import sys
from contextlib import contextmanager

import numpy as np

import field as fieldmod
import route
from crossing_search import ApproachTree, EdgeCosts, approach_tree


def fixture(*, water=False, relief=False):
    """Use the same canonical 65-sample field as crossing_search_cases."""
    h = np.full((65,65),30.6 if water else 40.0,dtype=np.float32)
    biome = np.ones((65,65),dtype=np.uint8)
    if water:
        h[:,28:32] = 29.9
    if relief:
        zz,xx = np.mgrid[:65,:65]
        h += (0.06*xx+0.02*zz).astype(np.float32)
        h[24:28,24:28] = 65.0
        h[8:12,16:40] = 30.8
        h[40:44,20:24] += np.arange(16,dtype=np.float32).reshape(4,4)
        swamp = next(key for key,value in route.BIOME_NAMES.items() if value=='Swamp')
        biome[32:48,24:48] = swamp
    field = fieldmod.Field('constructed-edge-costs',0.0,0.0,32.5,1.0,65,h,biome)
    corr = route.Corridor(field,cell_m=4.0)
    corr.forbid = np.zeros((corr.m,corr.m),dtype=bool)
    corr.forbid_all_water = not water
    if relief:
        corr.soft = np.zeros((corr.m,corr.m),dtype=np.float64)
        corr.soft[4:8,9] = 2.0
    return corr


@contextmanager
def native_evaluations():
    """Count the real method without replacing the policy being admitted."""
    code = route.Corridor._step_cost.__code__
    old = sys.getprofile()
    count = [0]
    def measured(frame,event,arg):
        if event=='call' and frame.f_code is code:
            count[0] += 1
        if old is not None:
            old(frame,event,arg)
    sys.setprofile(measured)
    try:
        yield count
    finally:
        sys.setprofile(old)


def route_cost(corr,path):
    if path is None:
        return None
    return sum(corr._step_cost(corr.cell_of(*a),corr.cell_of(*b),math.dist(a,b))
               for a,b in zip(path,path[1:]))


def edge_cost_cases():
    rows = []
    for name,water,relief in (
            ('diagonal_exact_route',False,False),
            ('directed_water_entry_and_exit',True,False),
            ('terrain_clamp_roughness_biome_and_soft_cost',False,True)):
        original = fixture(water=water,relief=relief)
        pool = EdgeCosts(original)
        corr = pool.corr
        start,goal = ((7,1),(7,14)) if water else ((1,1),(14,14))
        expected = ApproachTree(original,start).path_to(goal)
        actual = ApproachTree(corr,start,edge_costs=pool).path_to(goal)
        warm = ApproachTree(corr,start,edge_costs=pool).path_to(goal)
        extra = True
        if water:
            a,b = (7,6),(7,7)
            d = corr.cell_m
            extra = (corr.water[b] and not corr.water[a]
                     and corr._step_cost(a,b,d) != corr._step_cost(b,a,d))
        elif relief:
            extra = (bool(corr.fillable.any()) and bool(corr.rough.any())
                     and bool((corr.Bi!=1).any()) and bool(corr.soft.any())
                     and corr._step_cost((5,6),(6,6),corr.cell_m) is None)
        else:
            extra = any(a[0]!=b[0] and a[1]!=b[1] for a,b in zip(actual,actual[1:]))
        rows.append(dict(case=name,route=actual,cost=route_cost(corr,actual),
            matches_expected=actual is not None and actual==warm==expected
                and route_cost(corr,actual)==route_cost(original,expected) and bool(extra)))

    pool = EdgeCosts(fixture())
    base = pool.corr
    start,goal = (7,1),(7,14)
    baseline = ApproachTree(base,start).path_to(goal)
    base_tree = ApproachTree(base,start,edge_costs=pool)
    original_path = base_tree.path_to(goal)
    masked = copy.copy(base)
    masked.forbid = base.forbid.copy()
    masked.forbid[7,7] = True
    detour = ApproachTree(masked,start,edge_costs=pool).path_to(goal)
    expected = ApproachTree(masked,start).path_to(goal)
    wall = copy.copy(masked)
    wall.forbid = masked.forbid.copy()
    wall.forbid[:,7] = True
    refused = ApproachTree(wall,start,edge_costs=pool).path_to(goal)
    later = ApproachTree(masked,start,edge_costs=pool).path_to((6,14))
    rows.append(dict(case='changed_masks_refuse_wall_then_preserve_later_viable_route',
        matches_expected=baseline==original_path==base_tree.path_to(goal)
            and detour==expected and detour is not None and detour!=baseline
            and refused is None and ApproachTree(wall,start).path_to(goal) is None
            and later is not None and later==ApproachTree(masked,start).path_to((6,14))
            and all(masked.cell_of(*point)!=(7,7) for point in detour)))

    # A nearby first goal must not evaluate the whole grid. Repeating complete
    # masked searches may evaluate only previously unseen directed edges.
    pool = EdgeCosts(fixture())
    corr = pool.corr
    start = (7,1)
    with native_evaluations() as calls:
        ApproachTree(corr,start,edge_costs=pool).path_to(start)
    initial_calls = calls[0]
    goals = [(7,14),(1,14),(14,14),(7,14)]
    variants = []
    for row in (5,7,9):
        variant = copy.copy(corr)
        variant.forbid = corr.forbid.copy()
        variant.forbid[row,7] = True
        variants.append(variant)
    expected = [ApproachTree(variant,start).path_to(goal)
                for variant in variants for goal in goals]
    with native_evaluations() as calls:
        first = [ApproachTree(variant,start,edge_costs=pool).path_to(goal)
                 for variant in variants for goal in goals]
        first_calls = calls[0]
        second = [ApproachTree(variant,start,edge_costs=pool).path_to(goal)
                  for variant in variants for goal in reversed(goals)]
        repeated_calls = calls[0]-first_calls
    rows.append(dict(case='lazy_shared_native_edge_evaluation_bound',
        initial_native_calls=initial_calls,first_native_calls=first_calls,
        repeated_native_calls=repeated_calls,
        matches_expected=first==expected and all(path is not None for path in first)
            and second==[ApproachTree(variant,start).path_to(goal)
                          for variant in variants for goal in reversed(goals)]
            and initial_calls<=8 and first_calls<=8*corr.m*corr.m
            and repeated_calls==0))

    # The immutable copy neither freezes nor aliases the caller's terrain.
    source = fixture()
    pool = EdgeCosts(source)
    start,goal = (7,1),(7,14)
    ApproachTree(pool.corr,start,edge_costs=pool).path_to(goal)
    source.S[:,7] = 200.0
    changed = ApproachTree(source,start,edge_costs=pool).path_to(goal)
    rows.append(dict(case='changed_terrain_cannot_reuse_old_edge_values',
        matches_expected=changed is None and ApproachTree(source,start).path_to(goal) is None
            and ApproachTree(pool.corr,start,edge_costs=pool).path_to(goal) is not None))
    changed_corr = copy.copy(pool.corr)
    changed_corr.S = pool.corr.S.copy()
    changed_corr.S[:,7] = 200.0
    rows.append(dict(case='replaced_snapshot_terrain_falls_back_to_native',
        matches_expected=ApproachTree(changed_corr,start,edge_costs=pool).path_to(goal) is None))
    old_ceiling = route.GRADE_CEILING
    try:
        pool = EdgeCosts(source)
        before = ApproachTree(pool.corr,start,edge_costs=pool).path_to(goal)
        route.GRADE_CEILING = 100.0
        expected = ApproachTree(pool.corr,start).path_to(goal)
        with native_evaluations() as calls:
            changed = ApproachTree(pool.corr,start,edge_costs=pool).path_to(goal)
        rows.append(dict(case='changed_global_policy_uses_native_and_keeps_viable_route',
            native_calls=calls[0],matches_expected=before is None and expected is not None
                and changed==expected and calls[0]>0))
    finally:
        route.GRADE_CEILING = old_ceiling

    pool = EdgeCosts(fixture(relief=True))
    policy = copy.copy(pool.corr)
    policy.biome_penalty = dict(pool.corr.biome_penalty)
    policy.biome_penalty['Meadows'] = 13.0
    with native_evaluations() as calls:
        actual = ApproachTree(policy,(1,1),edge_costs=pool).path_to((14,14))
    expected = ApproachTree(policy,(1,1)).path_to((14,14))
    rows.append(dict(case='changed_local_cost_policy_falls_back_to_native',
        matches_expected=actual is not None and actual==expected and calls[0]>0
            and route_cost(policy,actual)!=route_cost(pool.corr,actual)))

    class CustomCorridor(route.Corridor):
        def _step_cost(self,a,b,d):
            if b[1]==7:
                return None
            return super()._step_cost(a,b,d)
    pool = EdgeCosts(fixture())
    ApproachTree(pool.corr,(7,1),edge_costs=pool).path_to((7,14))
    custom = copy.copy(pool.corr)
    custom.__class__ = CustomCorridor
    rows.append(dict(case='custom_corridor_falls_back_without_false_viable_route',
        matches_expected=ApproachTree(custom,(7,1),edge_costs=pool).path_to((7,14)) is None))
    overridden = copy.copy(pool.corr)
    original_step = overridden._step_cost
    overridden._step_cost = lambda a,b,d: None if b[1]==7 else original_step(a,b,d)
    rows.append(dict(case='instance_override_preserves_native_fallback',
        matches_expected=ApproachTree(overridden,(7,1),edge_costs=pool).path_to((7,14)) is None))
    rows.extend(approach_tree_cases())
    return rows


def approach_tree_cases():
    """Exercise alternating real searches, exhaustion, eviction and fallback."""
    pool = EdgeCosts(fixture())
    trees = {"edge_costs":pool}
    corr = pool.corr
    start,goal = (7,1),(7,14)
    a = copy.copy(corr)
    a.forbid = corr.forbid.copy()
    a.forbid[7,7] = True
    b = copy.copy(a)
    b.forbid = a.forbid.copy()
    b.forbid[6,7] = True
    masks = (a,b)
    expected = [ApproachTree(mask,start).path_to(goal) for mask in masks]
    base = approach_tree(trees,corr,start,base=True)
    base_path = base.path_to(goal)
    with native_evaluations() as calls:
        cold = [approach_tree(trees,mask,start,base=False).path_to(goal)
                for mask in masks]
        warmup_calls = calls[0]
        added_expansions = 0
        warm = []
        for mask in (a,b,a,b):
            selected = approach_tree(trees,mask,start,base=False)
            before = selected.expanded
            warm.append(selected.path_to(goal))
            added_expansions += selected.expanded-before
        added_calls = calls[0]-warmup_calls
    rows = [dict(case='alternating_exact_feedback_masks_retain_settled_searches',
        warmup_native_calls=warmup_calls,warm_native_calls=added_calls,
        warm_expansions=added_expansions,
        matches_expected=cold==expected and warm==expected+expected
            and all(path is not None for path in warm)
            and added_calls==0 and added_expansions==0)]

    wall = copy.copy(a)
    wall.forbid = a.forbid.copy()
    wall.forbid[:,7] = True
    refusal = approach_tree(trees,wall,start,base=False).path_to(goal)
    viable = approach_tree(trees,b,start,base=False).path_to(goal)
    with native_evaluations() as calls:
        selected_base = approach_tree(trees,corr,start,base=True)
        before = selected_base.expanded
        still_base = selected_base.path_to(goal)
    rows.append(dict(case='masked_exhaustion_does_not_poison_viable_or_base_routes',
        matches_expected=refusal is None and ApproachTree(wall,start).path_to(goal) is None
            and viable is not None and viable==expected[1] and still_base==base_path
            and selected_base.expanded-before==0 and calls[0]==0))

    # Request many distinct geometrically legal masks without revisiting A.
    # Test recomputation after eviction, not an incidental chosen LRU capacity.
    for i in range(corr.m):
        for j in range(corr.m):
            if (i,j) in (start,goal) or a.forbid[i,j]:
                continue
            other = copy.copy(a)
            other.forbid = a.forbid.copy()
            other.forbid[i,j] = True
            approach_tree(trees,other,start,base=False).path_to(goal)
    with native_evaluations() as calls:
        selected = approach_tree(trees,a,start,base=False)
        before = selected.expanded
        rebuilt = selected.path_to(goal)
        recomputed = selected.expanded-before
    rows.append(dict(case='evicted_mask_recomputes_exact_route_without_refusal',
        recomputed_expansions=recomputed,native_calls=calls[0],
        matches_expected=rebuilt is not None and rebuilt==expected[0] and recomputed>0))

    # Unknown/mutable data is evaluated afresh, not retained as a refusal.
    mutable = fixture()
    unsupported = {"edge_costs":pool}
    mutable.S[:,7] = 200.0
    refusal = approach_tree(unsupported,mutable,start,base=False).path_to(goal)
    mutable.S[:,7] = 40.0
    expected_viable = ApproachTree(mutable,start).path_to(goal)
    with native_evaluations() as calls:
        viable = approach_tree(unsupported,mutable,start,base=False).path_to(goal)
    rows.append(dict(case='unsupported_mutable_snapshot_cannot_cache_false_refusal',
        matches_expected=refusal is None and viable is not None
            and viable==expected_viable and calls[0]>0))

    # Even a compatible mask cannot carry an old bank's closed set into a new
    # immutable owner with different terrain.
    replacement = fixture()
    replacement.S[:,7] = 200.0
    next_pool = EdgeCosts(replacement)
    trees["edge_costs"] = next_pool
    changed = approach_tree(trees,next_pool.corr,start,base=True).path_to(goal)
    rows.append(dict(case='new_bank_snapshot_discards_previous_tree_state',
        matches_expected=base_path is not None and changed is None
            and ApproachTree(next_pool.corr,start).path_to(goal) is None))
    return rows
