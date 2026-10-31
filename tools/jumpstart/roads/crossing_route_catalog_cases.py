"""Constructed, parent-run catalog proofs; not actual-world/native-route evidence.

Run route_catalog_cases() with the roads directory on sys.path. Every returned
row's matches_expected must be true. No worlds, servers, files or managers used.
"""
from __future__ import annotations

import copy
import mmap
from types import MethodType

import numpy as np

import field as fieldmod
from crossing_route_catalog import RouteCatalog, native_frame_binding


def _immutable(array):
    return np.frombuffer(memoryview(array.tobytes()), dtype=array.dtype).reshape(array.shape)


def _native_field(*, step=1.0):
    n = 9
    return fieldmod.Field("constructed-route-catalog", 0.0, 0.0, n * step / 2,
        step, n, _immutable(np.full((n, n), 40.0, dtype="<f4")),
        _immutable(np.ones((n, n), dtype=np.uint8)))


def _record(offset=0.0, *, stations=3):
    nodes = np.column_stack((np.arange(stations, dtype=float) + offset,
                             np.arange(stations, dtype=float) * 2.0))
    return dict(nodes=nodes, goal_xz=list(nodes[-1]), bank_snap_m=0.25,
                water_reroute_rounds=2, details={"feedback": ["dry after reroute"]})


def _same_routes(left, right):
    return len(left) == len(right) and all(
        np.array_equal(a["nodes"], b["nodes"])
        and {k: v for k, v in a.items() if k != "nodes"}
            == {k: v for k, v in b.items() if k != "nodes"}
        for a, b in zip(left, right))


def route_catalog_cases():
    rows = []

    def add(name, result, **evidence):
        rows.append(dict(case=name, matches_expected=bool(result), **evidence))

    frame = _native_field()
    state = {"policy": ("canonical-constructed-policy", 1), "coarse": b"coarse-a"}
    frames = [frame]
    binding = lambda: native_frame_binding(frames, policy_token=state["policy"])
    signature = lambda: (state["policy"], state["coarse"], 30.6)
    work = [0]

    def producer(stats):
        work[0] += 1
        stats.update(eligible_graph_goals=4, goals_considered=1, route_queries=1,
                     base_search_expansions=7)
        yield _record()
        stats.update(goals_considered=2, route_queries=2, router_refused=1)
        yield None
        stats.update(goals_considered=3, route_queries=3,
                     optional_goals_uncovered=1,
                     representative_optional_coverage="unread rendered Heightmap vertex (9, 9)")
        stats["goals_considered"] = 4
        stats["route_queries"] = 4
        stats["base_search_expansions"] = 11
        yield _record(5.0)
        stats["routing_complete"] = True

    catalog = RouteCatalog()

    def run(cache=catalog, *, stats=None, source=producer, key=None):
        stats = {} if stats is None else stats
        routes = list(cache.routes(signature=signature() if key is None else key,
            current_signature=signature if key is None else None,
            binding=binding, producer=source, stats=stats))
        return routes, stats

    cold, cold_stats = run()
    warm, warm_stats = run()
    logical = lambda s: {k: v for k, v in s.items()
                         if k not in {"route_queries", "base_search_expansions",
                                      "route_catalog_reuses"}}
    add("complete_replay_preserves_routes_refusals_and_optional_coverage",
        work[0] == 1 and catalog.entry_count == 1 and _same_routes(cold, warm)
        and logical(cold_stats) == logical(warm_stats)
        and warm_stats["route_queries"] == warm_stats["base_search_expansions"] == 0
        and warm_stats["optional_goals_uncovered"] == 1,
        producer_runs=work[0], replay_route_queries=warm_stats["route_queries"])

    # Mutate both the initial producer result and a replay, including nested data.
    cold[0]["nodes"][:] = -100
    warm[0]["goal_xz"][0] = -200
    warm[0]["details"]["feedback"].append("caller mutation")
    fresh, _ = run()
    add("replay_geometry_and_nested_metadata_are_mutation_isolated",
        _same_routes(fresh, [_record(), _record(5.0)]) and work[0] == 1)

    isolated = RouteCatalog()
    public = {}
    stream = isolated.routes(signature=signature(), binding=binding,
                             producer=producer, stats=public)
    next(stream)
    public.update(legal_approach_plans=7, optional_construction_uncovered=2,
                  refusal_counts={"side_writer": 10},
                  representative_refusals={"side_writer": {"ok": False}})
    list(stream)
    _, clean = run(isolated)
    add("side_compiler_counters_never_enter_routing_catalog",
        "refusal_counts" not in clean and "legal_approach_plans" not in clean
        and "optional_construction_uncovered" not in clean
        and "representative_refusals" not in clean
        and public["refusal_counts"] == {"side_writer": 10})

    # Unknown optional coverage is NOT inferred to be zero on replay.
    for label, value in (("missing", "missing"), ("explicit_unknown", None)):
        unknown_cache = RouteCatalog()
        def unknown(stats, value=value):
            if value is None:
                stats["optional_goals_uncovered"] = None
            yield _record()
        run(unknown_cache, source=unknown)
        _, unknown_stats = run(unknown_cache, source=unknown)
        add("optional_coverage_" + label + "_stays_unknown",
            ("optional_goals_uncovered" not in unknown_stats if value == "missing"
             else unknown_stats.get("optional_goals_uncovered", "missing") is None))

    # The same bytes content in a NEW root/frame/data view is a different binding.
    before = work[0]
    frame.h = _immutable(frame.h)
    run()
    state["policy"] = ("canonical-constructed-policy", 2)
    run()
    state["coarse"] = b"coarse-b"
    run()
    add("replacing_bytes_policy_or_coarse_signature_recomputes", work[0] == before + 3)

    original = binding()
    metadata_ok = True
    for name, replacement in (("cx", 1.0), ("cz", 2.0), ("half", 6.0), ("step", 2.0)):
        old = getattr(frame, name)
        setattr(frame, name, replacement)
        metadata_ok &= binding() is not None and binding() != original
        setattr(frame, name, old)
    old_h = frame.h
    for replacement in (old_h.T, old_h.astype(np.float64), old_h[:-1],
                        _immutable(old_h).view()):
        frame.h = replacement
        metadata_ok &= binding() != original
    frame.h = old_h
    coarse = _native_field(step=4.0)
    add("coordinates_dtype_shape_strides_view_identity_and_coarse_frame_are_bound",
        metadata_ok and native_frame_binding((frame, coarse)) is not None)

    # No purported readonly view may hide mutable or foreign backing storage.
    mutable_cases = []
    writable = frame.h.copy()
    mutable_cases.append(("writable", writable))
    readonly_view = writable.view()
    readonly_view.flags.writeable = False
    mutable_cases.append(("readonly_view_of_writable_array", readonly_view))
    readonly_owner = frame.h.copy()
    readonly_owner.flags.writeable = False
    mutable_cases.append(("readonly_owning_ndarray_can_be_made_writable", readonly_owner))
    raw = bytearray(frame.h.tobytes())
    hidden = np.frombuffer(memoryview(raw).toreadonly(), dtype="<f4").reshape(frame.h.shape)
    mutable_cases.append(("readonly_memoryview_of_bytearray", hidden))
    class ArrayOverride(np.ndarray):
        pass
    mutable_cases.append(("ndarray_subclass", frame.h.view(ArrayOverride)))
    for label, replacement in mutable_cases:
        frame.h = replacement
        disabled = RouteCatalog()
        before = work[0]
        run(disabled)
        run(disabled)
        add(label + "_opts_out", binding() is None and disabled.entry_count == 0
            and work[0] == before + 2)
    frame.h = old_h
    mapped = mmap.mmap(-1, frame.h.nbytes)
    mapped.write(frame.h.tobytes())
    mapped_view = np.frombuffer(mapped, dtype="<f4").reshape(frame.h.shape)
    mapped_view.flags.writeable = False
    frame.h = mapped_view
    add("readonly_mmap_opts_out", binding() is None)
    frame.h = old_h
    del mapped_view
    mapped.close()
    frame.at = MethodType(lambda self, x, z: 99.0, frame)
    add("instance_sampling_override_opts_out", binding() is None)
    del frame.at
    class FieldOverride(fieldmod.Field):
        pass
    custom = copy.copy(frame)
    custom.__class__ = FieldOverride
    add("custom_field_type_opts_out", native_frame_binding((custom,)) is None)

    original_biome = frame.biome
    frame.biome = original_biome.copy()
    add("mutable_biome_also_opts_out", binding() is None)
    frame.biome = original_biome
    old_binding = binding()
    frames[0] = copy.copy(frame)
    add("replacing_native_frame_object_invalidates_identity", binding() != old_binding)
    frames[0] = frame

    # close(), throw(), and producer exceptions must not publish a prefix.
    for operation in ("close", "throw", "exception"):
        partial = RouteCatalog()
        failure = RuntimeError("constructed routing failure")
        attempts = [0]
        def incomplete(stats):
            attempts[0] += 1
            yield _record()
            if operation == "exception":
                raise failure
            yield _record(5.0)
        stream = partial.routes(signature=signature(), binding=binding,
                                producer=incomplete, stats={})
        next(stream)
        same_error = True
        try:
            if operation == "close":
                stream.close()
            elif operation == "throw":
                stream.throw(failure)
            else:
                next(stream)
        except RuntimeError as error:
            same_error = error is failure
        unpublished = partial.entry_count == 0 and partial.pending_byte_count == 0
        run(partial)
        add(operation + "_cannot_replay_partial_state",
            same_error and unpublished and attempts[0] == 1 and partial.entry_count == 1)

    # A non-generator producer may report failure only from close().
    close_cache = RouteCatalog()
    close_failure = RuntimeError("constructed producer close failure")
    class ClosingFailure:
        def __init__(self):
            self.records = iter([_record()])
        def __iter__(self):
            return self
        def __next__(self):
            return next(self.records)
        def close(self):
            raise close_failure
    close_error = False
    try:
        run(close_cache, source=lambda stats: ClosingFailure())
    except RuntimeError as error:
        close_error = error is close_failure
    add("producer_close_exception_cannot_publish_complete_looking_entry",
        close_error and close_cache.entry_count == 0 and close_cache.pending_byte_count == 0)

    for change in ("frame", "policy", "coarse"):
        changing = RouteCatalog()
        stream = changing.routes(signature=signature(), current_signature=signature,
            binding=binding, producer=producer, stats={})
        next(stream)
        if change == "frame":
            frame.cx += 1.0
        elif change == "policy":
            state["policy"] = (*state["policy"], "changed")
        else:
            state["coarse"] += b"changed"
        list(stream)
        add(change + "_replacement_while_suspended_cannot_admit",
            changing.entry_count == 0 and changing.pending_byte_count == 0)

    replay_changing = RouteCatalog()
    run(replay_changing)
    stream = replay_changing.routes(signature=signature(), current_signature=signature,
        binding=binding, producer=producer, stats={})
    next(stream)
    state["coarse"] += b"replay-change"
    invalidated = False
    try:
        next(stream)
    except RuntimeError:
        invalidated = True
    add("changed_replay_inputs_cannot_splice_old_and_new_routes",
        invalidated and replay_changing.entry_count == 0)

    entry_limited = RouteCatalog(max_entries=1)
    before = work[0]
    run(entry_limited, key="a")
    run(entry_limited, key="b")
    run(entry_limited, key="a")
    add("entry_eviction_recomputes_without_shortening_search",
        work[0] == before + 3 and entry_limited.entry_count == 1)

    for limit in (0, 16, 1200):
        limited = RouteCatalog(max_bytes=limit)
        before = work[0]
        a, sa = run(limited)
        b, sb = run(limited)
        add("byte_admission_" + str(limit) + "_never_truncates_enumeration",
            work[0] == before + 2 and _same_routes(a, b) and len(a) == 2
            and sa["routing_complete"] and sb["routing_complete"]
            and limited.byte_count <= limit and limited.pending_byte_count == 0)

    byte_limited = RouteCatalog(max_bytes=65536)
    big_work = [0]
    def big(stats):
        big_work[0] += 1
        yield _record(stations=1024)
    for i in range(10):
        run(byte_limited, key="key-" + str(i), source=big)
    before = big_work[0]
    run(byte_limited, key="key-0", source=big)
    add("byte_eviction_recomputes_complete_geometry",
        big_work[0] == before + 1 and 0 < byte_limited.entry_count < 10
        and byte_limited.byte_count <= 65536 and byte_limited.pending_byte_count == 0)

    # Multiple bank generators share a global pending/admitted byte budget.
    concurrent = RouteCatalog(max_bytes=65536)
    streams = [concurrent.routes(signature=("concurrent", i), binding=binding,
        producer=big, stats={}) for i in range(8)]
    first = [next(stream) for stream in streams]
    bounded = concurrent.byte_count + concurrent.pending_byte_count <= 65536
    for stream in streams:
        list(stream)
    add("pending_bank_enumerations_share_admission_budget",
        bounded and all(len(record["nodes"]) == 1024 for record in first)
        and concurrent.byte_count <= 65536 and concurrent.pending_byte_count == 0)

    # Empty normally exhausted enumerations are reusable, not side-failure caches.
    empty_work = [0]
    def empty(stats):
        empty_work[0] += 1
        stats.update(router_refused=5, goals_considered=5, route_queries=5,
                     base_search_expansions=17)
        yield None
    empty_cache = RouteCatalog()
    a, _ = run(empty_cache, source=empty)
    b, stats = run(empty_cache, source=empty)
    add("complete_empty_route_enumeration_replays_intrinsic_refusals",
        a == b == [] and empty_work[0] == 1 and stats["router_refused"] == 5
        and stats["route_queries"] == stats["base_search_expansions"] == 0)
    # Different near/far frame wrappers can refer to the SAME borrowed byte set.
    # Preserve both exact keys, but never retain completed entries from old roots.
    root_cache = RouteCatalog()
    before = work[0]
    run(root_cache)
    borrowed_roots = binding().root_ids
    frames[0] = copy.copy(frame)
    run(root_cache)
    frames[0] = frame
    run(root_cache)
    add("same_root_metadata_variants_preserve_useful_bank_catalogs",
        binding().root_ids == borrowed_roots and work[0] == before + 2
        and root_cache.entry_count == 2)
    previous_height = frame.h
    frame.h = _immutable(frame.h)
    run(root_cache)
    add("new_root_owner_set_releases_all_old_snapshot_entries",
        binding().root_ids != borrowed_roots and root_cache.entry_count == 1
        and all(entry.binding.root_ids == binding().root_ids
                for entry in root_cache._entries.values()))
    frame.h = frame.h.copy()
    run(root_cache)
    add("unsupported_binding_releases_borrowed_snapshot_and_entries",
        root_cache.entry_count == 0 and root_cache._binding is None)
    frame.h = previous_height

    rows.extend(_native_routing_consumer_cases())
    return rows


def _native_routing_consumer_cases():
    """Exercise actual routing, not a producer that echoes cache inputs."""
    from crossing_physical import PhysicalFields
    from crossing_reachability_cases import fixture

    source, graph, bank = fixture(n=33)
    native = source.fields[0]
    native.h = _immutable(native.h)
    native.biome = _immutable(native.biome)
    physical = PhysicalFields({native.id: native},
        {"constructed": True, "surface": "heightmap"}, keepout_discs=())
    cold_stats, warm_stats = {}, {}
    cold = list(physical._route_paths(graph, bank, cold_stats))
    warm = list(physical._route_paths(graph, bank, warm_stats))
    same = _same_routes(cold, warm)
    # Actual routes must still start at the bank and reach their handoff.
    geometry = bool(cold) and all(
        np.array_equal(record["nodes"][0], bank["xz"])
        and np.array_equal(record["nodes"][-1], record["goal_xz"])
        for record in warm)
    return [dict(case="native_complete_routing_replays_without_search",
        matches_expected=bool(same and geometry
            and cold_stats.get("route_queries", 0) > 0
            and warm_stats.get("route_queries") == 0
            and warm_stats.get("route_catalog_reuses") == 1
            and cold_stats.get("goals_considered") == warm_stats.get("goals_considered")
            and cold_stats.get("optional_goals_uncovered")
                == warm_stats.get("optional_goals_uncovered")))]
