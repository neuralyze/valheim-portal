"""Constructed routing proofs and work bounds; not measured-world evidence."""
import numpy as np
import field as fieldmod
import route
from geography import RoadGraph
from crossing_physical import PhysicalFields


class CountedPhysical(PhysicalFields):
    def routed_approach(self, *args, **kwargs):
        self.route_queries += 1
        return super().routed_approach(*args, **kwargs)


def fixture(n=129):
    field = fieldmod.Field('constructed-reachability', 0.0, 0.0, n/2, 1.0, n,
        np.full((n,n),40.0,dtype=np.float32),np.ones((n,n),dtype=np.uint8))
    graph = RoadGraph(field,forbid=np.zeros((n//4,n//4),dtype=bool))
    physical = CountedPhysical({field.id:field},
        {'constructed':True,'surface':'heightmap'},keepout_discs=())
    physical.route_queries = 0
    return physical, graph, {'xz':[0.0,0.0],'level_y':40.0}


def isolate(graph, bank):
    i,j = graph.corr.nearest_allowed(*bank['xz'])
    ring = np.zeros_like(graph.ok)
    ring[i-1:i+2,j-1:j+2] = True
    ring[i,j] = False
    graph.corr.S[ring] = 200.0
    return ring


def first_path(physical, graph, bank):
    stream = physical._route_paths(graph,bank,{})
    try:
        return next(stream,None)
    finally:
        stream.close()


def reachability_cases():
    rows = []
    physical,graph,bank = fixture()
    ring = isolate(graph,bank)
    stats = {}
    paths = list(physical._route_paths(graph,bank,stats))
    first_queries = physical.route_queries
    physical.route_queries = 0
    repeated = list(physical._route_paths(graph,bank,{}))
    rows.append(dict(case='exhausted_tree_proves_unreachable_handoffs_in_bulk',
        eligible_goals=stats.get('eligible_graph_goals'),route_queries=first_queries,
        repeated_route_queries=physical.route_queries,
        matches_expected=not paths and not repeated and first_queries<=1
            and physical.route_queries==0 and stats['eligible_graph_goals']>100))
    graph.corr.S[ring] = 40.0
    path = first_path(physical,graph,bank)
    rows.append(dict(case='changed_corridor_heights_invalidate_empty_bank_proof',
        matches_expected=path is not None and len(path['nodes'])>=2))

    physical,graph,bank = fixture()
    ring = isolate(graph,bank)
    list(physical._route_paths(graph,bank,{}))
    old_ceiling = route.GRADE_CEILING
    try:
        route.GRADE_CEILING = 100.0
        path = first_path(physical,graph,bank)
    finally:
        route.GRADE_CEILING = old_ceiling
    rows.append(dict(case='changed_grade_policy_invalidates_empty_bank_proof',
        matches_expected=path is not None and len(path['nodes'])>=2))

    physical,graph,bank = fixture()
    i,j = graph.corr.nearest_allowed(*bank['xz'])
    ring = np.zeros_like(graph.ok)
    ring[i-1:i+2,j-1:j+2] = True
    ring[i,j] = False
    graph.ok[ring] = False
    list(physical._route_paths(graph,bank,{}))
    graph.ok[ring] = True
    path = first_path(physical,graph,bank)
    rows.append(dict(case='changed_keepout_mask_invalidates_empty_bank_proof',
        matches_expected=path is not None and len(path['nodes'])>=2))

    physical,graph,bank = fixture()
    ii,jj = np.nonzero(graph.ok & ~graph.corr.water)
    x,z = graph.corr.x0+jj*graph.corr.cell_m,graph.corr.z0+ii*graph.corr.cell_m
    distance = np.hypot(x,z)
    eligible = np.flatnonzero(distance>=8.0)
    target = eligible[np.argmin(distance[eligible])]
    graph.corr.S[ii[target],jj[target]] = 200.0
    path = first_path(physical,graph,bank)
    rows.append(dict(case='later_reachable_handoff_survives_exhausted_tree_pruning',
        matches_expected=bool(path is not None and graph.corr.S[
            graph.corr.cell_of(*path['goal_xz'])]<100.0)))

    from contextlib import closing
    from crossing_parallel import ordered_candidates
    physical,graph,bank = fixture(257)
    isolate(graph,bank)
    physical.bind_approach_graphs(graph,graph)
    crossing = {'id':'constructed-evidence'}
    request = dict(near_bank={'xz':bank['xz']},far_bank={'xz':[0.0,22.0]},datum=40.0)
    cold = physical.candidate(crossing,request['near_bank'],request['far_bank'],40.0)
    warm = physical.candidate(crossing,request['near_bank'],request['far_bank'],40.0)
    other,other_graph,_ = fixture(257)
    isolate(other_graph,bank)
    other.bind_approach_graphs(other_graph,other_graph)
    with closing(ordered_candidates(other,crossing,[request]*4,2)) as results:
        parallel = [proof for _request,proof in results]
    rows.append(dict(case='geometric_evidence_is_independent_of_cache_and_worker_assignment',
        matches_expected=not cold['ok'] and cold==warm
            and all(proof==cold for proof in parallel)))
    return rows
