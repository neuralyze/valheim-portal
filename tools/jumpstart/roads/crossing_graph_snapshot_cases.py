"""Constructed ownership/routing regressions, never measured-world evidence."""
import copy
import gc
from pathlib import Path
import sys
from types import SimpleNamespace
import weakref

import numpy as np

from crossing_graph_snapshot import native_routing_signature, routing_snapshot
from crossing_physical import PhysicalFields
from crossing_reachability_cases import fixture, isolate
from crossing_route_catalog_cases import _same_routes
from crossing_search import empty_bank_key


def _immutable(array):
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def _native(n=33):
    source, graph, bank = fixture(n)
    frame = source.fields[0]
    frame.h = _immutable(frame.h)
    frame.biome = _immutable(frame.biome)
    physical = PhysicalFields({frame.id: frame},
        {"constructed": True, "surface": "heightmap"}, keepout_discs=())
    return physical, graph, bank


def _signature(graph, bank):
    return native_routing_signature(graph, bank, 8.0)


def graph_snapshot_cases():
    rows = []

    def add(name, result, **evidence):
        rows.append(dict(case=name, matches_expected=bool(result), **evidence))

    _physical, graph, bank = _native()
    graph.metadata = {"nested": [{"root": [1.0, 2.0]}]}
    graph.corr.metadata = {"ordered": ["first", "second"]}
    snapshot = routing_snapshot(graph)
    numeric_equal = True
    original_writable = True
    immutable = True
    independent = True
    for source, target in ((graph, snapshot), (graph.corr, snapshot.corr)):
        for name, value in vars(source).items():
            if type(value) is not np.ndarray:
                continue
            owned = getattr(target, name)
            numeric_equal &= (owned.dtype == value.dtype and owned.shape == value.shape
                              and np.array_equal(owned, value))
            original_writable &= value.flags.writeable
            for view in (owned, owned.base):
                try:
                    view.flags.writeable = True
                except ValueError:
                    pass
                else:
                    immutable = False
            before = owned.copy()
            old_value = value.flat[0]
            value.flat[0] = not value.flat[0] if value.dtype.kind == "b" else 17
            independent &= np.array_equal(owned, before)
            value.flat[0] = old_value
    graph.metadata["nested"][0]["root"][0] = 99.0
    graph.corr.metadata["ordered"].append("third")
    graph.corr.biome_penalty["Meadows"] = 9.0
    add("snapshot_numeric_equality_and_bytes_ownership",
        snapshot is not graph and snapshot.corr is not graph.corr
        and snapshot.corr.fld is graph.corr.fld and numeric_equal
        and original_writable and immutable and independent
        and snapshot.corr.wet is snapshot.corr.water
        and snapshot.metadata == {"nested": [{"root": [1.0, 2.0]}]}
        and snapshot.corr.metadata == {"ordered": ["first", "second"]}
        and snapshot.corr.biome_penalty["Meadows"] == 1.0)

    physical, graph, bank = _native()
    snapshot = routing_snapshot(graph)
    initial = _signature(snapshot, bank)
    add("owned_signature_is_hashable_stable_and_idempotent",
        initial is not None and hash(initial) == hash(_signature(snapshot, bank))
        and initial == _signature(snapshot, bank)
        and routing_snapshot(snapshot) is snapshot
        and _signature(graph, bank) is None)
    mutations = []
    for target, name, value in (
            (snapshot, "big", snapshot.big + 1),
            (snapshot, "cls", "spur"),
            (snapshot, "root", (1.0, 2.0)),
            (snapshot, "network_id", snapshot.network_id + 1),
            (snapshot, "trunk_land_id", snapshot.trunk_land_id + 1),
            (snapshot.corr, "x0", snapshot.corr.x0 + 1),
            (snapshot.corr, "cell_m", snapshot.corr.cell_m + 1),
            (snapshot.corr, "grade_max", snapshot.corr.grade_max + 0.01),
            (snapshot.corr, "forbid_all_water", True)):
        old = getattr(target, name)
        setattr(target, name, value)
        changed = _signature(snapshot, bank)
        mutations.append(changed is None or changed != initial)
        setattr(target, name, old)
    snapshot.corr.biome_penalty["Meadows"] = 2.0
    mutations.append(_signature(snapshot, bank) != initial)
    snapshot.corr.biome_penalty["Meadows"] = 1.0
    snapshot.metadata = {"nested": [1, {"value": 2}]}
    plain = _signature(snapshot, bank)
    snapshot.metadata["nested"][1]["value"] = 3
    mutations.append(_signature(snapshot, bank) != plain)
    del snapshot.metadata
    mutations.append(native_routing_signature(snapshot,
        dict(bank, xz=[1.0, 0.0]), 8.0) != initial)
    mutations.append(native_routing_signature(snapshot, bank, 9.0) != initial)
    add("scalar_container_class_root_geometry_bank_changes_invalidate",
        all(mutations) and _signature(snapshot, bank) == initial)

    replacements = []
    for target in (snapshot, snapshot.corr):
        for name, value in tuple(vars(target).items()):
            if type(value) is not np.ndarray:
                continue
            setattr(target, name, value.copy())
            replacements.append(_signature(snapshot, bank) is None)
            setattr(target, name, value)
    snapshot.corr.forbid = np.zeros_like(snapshot.ok)
    replacements.append(_signature(snapshot, bank) is None)
    snapshot.corr.forbid = None
    old_shape = snapshot.corr.S.shape
    snapshot.corr.S.shape = (snapshot.corr.S.size,)
    replacements.append(_signature(snapshot, bank) is None)
    snapshot.corr.S.shape = old_shape
    old_corr = snapshot.corr
    snapshot.corr = copy.copy(old_corr)
    replacements.append(_signature(snapshot, bank) is None)
    snapshot.corr = old_corr
    old_frame = snapshot.corr.fld
    snapshot.corr.fld = copy.copy(old_frame)
    replacements.append(_signature(snapshot, bank) is None)
    snapshot.corr.fld = old_frame
    add("every_coarse_array_replacement_and_header_edit_opts_out",
        all(replacements) and _signature(snapshot, bank) == initial,
        checked_replacements=len(replacements))

    field_binding = physical._route_catalog_binding(snapshot)
    old_height = old_frame.h
    old_frame.h = old_frame.h.copy()
    writable_binding = physical._route_catalog_binding(snapshot)
    old_frame.h = old_height
    add("native_field_is_borrowed_and_existing_frame_checks_remain_required",
        snapshot.corr.fld is old_frame and field_binding is not None
        and writable_binding is None
        and physical._route_catalog_binding(snapshot) == field_binding)

    class CustomGraph(type(graph)):
        pass

    class CustomCorridor(type(graph.corr)):
        pass

    custom_graph = copy.copy(graph)
    custom_graph.__class__ = CustomGraph
    custom_corr = copy.copy(graph)
    custom_corr.corr = copy.copy(graph.corr)
    custom_corr.corr.__class__ = CustomCorridor
    override = copy.copy(graph)
    override.corr = copy.copy(graph.corr)
    override.corr.blocked = lambda i, j: True
    unknown = copy.copy(graph)
    unknown.metadata = object()
    object_array = copy.copy(graph)
    object_array.extra = np.array([object()], dtype=object)
    unsupported = [custom_graph, custom_corr, override, unknown, object_array,
                   SimpleNamespace(corr=graph.corr)]
    add("unsupported_graphs_corridors_and_metadata_remain_unadapted",
        all(routing_snapshot(value) is value
            and _signature(value, bank) is None for value in unsupported))
    fallback = copy.copy(graph.corr)
    fallback.forbid = ~graph.ok
    fallback.forbid_all_water = True
    before = empty_bank_key(graph, fallback, bank, 8.0)
    fallback.S = fallback.S.copy()
    fallback.S.flat[0] += 1.0
    after = empty_bank_key(graph, fallback, bank, 8.0)
    add("unsupported_owned_signature_keeps_conservative_hash_fallback",
        _signature(graph, bank) is None and before is not None and before != after)

    # Tokens outlive their input graphs here, as they do in a route catalog.
    def retained_token():
        _physical, source, bank = _native()
        owned = routing_snapshot(source)
        references = [weakref.ref(value) for target in (owned, owned.corr)
                      for value in vars(target).values()
                      if type(value) is np.ndarray]
        return _signature(owned, bank), references

    token, references = retained_token()
    gc.collect()
    add("catalog_tokens_do_not_retain_old_coarse_snapshots",
        token is not None and all(reference() is None for reference in references))

    # The same native router and enumerator, not a producer echoing its input.
    physical, graph, bank = _native()
    snapshot = routing_snapshot(graph)
    source_stats, owned_stats = {}, {}
    original = list(physical._route_paths_uncached(graph, bank, source_stats))
    owned = list(physical._route_paths_uncached(snapshot, bank, owned_stats))
    add("actual_complete_routing_order_geometry_and_stats_are_unchanged",
        bool(original) and _same_routes(original, owned) and source_stats == owned_stats,
        routes=len(original))
    cold_stats, warm_stats = {}, {}
    cold = list(physical._route_paths(snapshot, bank, cold_stats))
    warm = list(physical._route_paths(snapshot, bank, warm_stats))
    add("owned_routes_replay_without_changing_geometric_evidence",
        _same_routes(original, cold) and _same_routes(cold, warm)
        and cold_stats.get("route_queries", 0) > 0
        and warm_stats.get("route_queries") == 0
        and warm_stats.get("route_catalog_reuses") == 1
        and cold_stats.get("goals_considered") == warm_stats.get("goals_considered")
        and cold_stats.get("optional_goals_uncovered")
            == warm_stats.get("optional_goals_uncovered"))

    physical, graph, bank = _native(257)
    isolate(graph, bank)
    snapshot = routing_snapshot(graph)
    crossing = {"id": "constructed-owned-snapshot-evidence"}
    near, far = {"xz": list(bank["xz"])}, {"xz": [0.0, 22.0]}
    physical.bind_approach_graphs(graph, graph)
    original_proof = physical.candidate(crossing, near, far, 40.0)
    physical.bind_approach_graphs(snapshot, snapshot)
    owned_proof = physical.candidate(crossing, near, far, 40.0)
    replay_proof = physical.candidate(crossing, near, far, 40.0)
    add("snapshot_ownership_and_replay_do_not_create_false_geometry_proof",
        not original_proof["ok"] and original_proof == owned_proof == replay_proof)
    rows.extend(script_graph_snapshot_cases())
    return rows


def script_graph_snapshot_cases():
    """Extra proof when run by the real ``python geography.py selftest`` CLI."""
    import geography

    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if path is None or Path(path).resolve() != Path(geography.__file__).resolve():
        return []
    script_class = main.RoadGraph
    imported_class = geography.RoadGraph
    _physical, imported_graph, bank = _native()
    script_graph = script_class(imported_graph.corr.fld,
        forbid=np.zeros_like(imported_graph.ok))
    script_snapshot = routing_snapshot(script_graph)
    imported_snapshot = routing_snapshot(imported_graph)
    script_token = _signature(script_snapshot, bank)
    imported_token = _signature(imported_snapshot, bank)
    admitted = (script_class is not imported_class
                and script_snapshot is not script_graph
                and imported_snapshot is not imported_graph
                and script_token is not None and imported_token is not None)
    replacements = []
    try:
        main.RoadGraph = type("ChangedScriptGraph", (script_class,), {})
        replacements.append(_signature(script_snapshot, bank) is None
            and _signature(imported_snapshot, bank) == imported_token)
        main.RoadGraph = script_class
        geography.RoadGraph = type("ChangedImportedGraph", (imported_class,), {})
        replacements.append(_signature(imported_snapshot, bank) is None
            and _signature(script_snapshot, bank) == script_token)
        geography.RoadGraph = imported_class
        main.__file__ = str(Path(path).with_name("not-geography.py"))
        replacements.append(_signature(script_snapshot, bank) is None)
    finally:
        main.RoadGraph = script_class
        geography.RoadGraph = imported_class
        main.__file__ = path
    return [dict(case="real_script_and_imported_native_graphs_admit_concurrently",
        matches_expected=bool(admitted and all(replacements)
            and _signature(script_snapshot, bank) == script_token
            and _signature(imported_snapshot, bank) == imported_token))]
