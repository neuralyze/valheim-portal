"""Opt-in coarse routing ownership, not geometry or physical-refusal evidence.

Only exact native RoadGraph/Corridor instances are adapted. Their numeric arrays
are copied once into immutable bytes, while their Field remains borrowed and
subject to PhysicalFields._route_catalog_binding's existing native frame checks.
Unsupported graphs/metadata stay on the conservative content-hashing path.

Signatures retain only a small unique identity and immutable metadata, NEVER an
array, graph, Field or snapshot owner. Old catalog keys therefore cannot retain
replaced coarse buffers outside the catalog's byte budget. A changed/replaced
array opts out rather than copying or hashing a grid during generator yields.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, fields
from types import MappingProxyType
import math
from pathlib import Path
import sys
import weakref

import numpy as np

import field as fieldmod
import geography
import route


_NATIVE_GRAPH = geography.RoadGraph
_NATIVE_CORRIDOR = route.Corridor
_NATIVE_FIELD = fieldmod.Field
_MARKER = "_routing_snapshot"


def _script_graph():
    """Recognize only this sibling geography.py's native script entrypoint."""
    try:
        module = sys.modules.get("__main__")
        sibling = Path(geography.__file__).resolve()
        if (module is None or Path(module.__file__).resolve() != sibling):
            return None
        cls = module.RoadGraph
        if (type(cls) is not type or cls.__module__ != "__main__"
                or Path(cls.__init__.__code__.co_filename).resolve() != sibling):
            return None
        return cls
    except (AttributeError, OSError, TypeError, ValueError):
        return None


# Lowercase: these are class identities, not plain serializable policy constants.
_main_graph = _script_graph()
_main_module = sys.modules.get("__main__")
_main_file = getattr(_main_module, "__file__", None)


def _native_graph(graph):
    if type(graph) is _NATIVE_GRAPH:
        return geography.RoadGraph is _NATIVE_GRAPH
    return (_main_graph is not None and type(graph) is _main_graph
            and sys.modules.get("__main__") is _main_module
            and getattr(_main_module, "RoadGraph", None) is _main_graph
            and getattr(_main_module, "__file__", None) == _main_file)


def _plain(value, *, clone=False):
    """Copy/tokenize plain metadata without invoking custom object semantics."""
    kind = type(value)
    if kind in (type(None), bool, int, float, str, bytes):
        if kind is float and not math.isfinite(value):
            raise ValueError("nonfinite routing metadata")
        if clone:
            return value
        return (kind, value.hex() if kind is float else value)
    if kind in (tuple, list):
        items = [_plain(item, clone=clone) for item in value]
        return kind(items) if clone else (kind, tuple(items))
    if kind is dict:
        items = [(_plain(key, clone=clone), _plain(item, clone=clone))
                 for key, item in value.items()]
        return dict(items) if clone else (kind, tuple(items))
    if kind in (set, frozenset):
        items = [_plain(item, clone=clone) for item in value]
        return kind(items) if clone else (kind, frozenset(items))
    raise TypeError("noncanonical routing metadata")


def _layout(value):
    """Small descriptors detect even in-place ndarray header changes."""
    if (type(value) is not np.ndarray or value.dtype.kind not in "biufc"
            or value.dtype.metadata is not None or value.dtype.fields is not None
            or value.dtype.subdtype is not None or value.flags.writeable):
        raise TypeError("noncanonical routing array")
    return (value.dtype.str, value.shape, value.strides,
            value.__array_interface__["data"][0], id(value.base))


@dataclass(frozen=True, eq=False)
class _Snapshot:
    identity: object
    graph: weakref.ReferenceType
    corr: weakref.ReferenceType
    frame: weakref.ReferenceType
    arrays: MappingProxyType


def routing_snapshot(graph):
    """Clone native routing inputs without freezing/mutating caller data.

    Each graph owns one copy of each distinct top-level numeric array (aliases
    such as Corridor.wet/water stay aliases). Plain mutable containers are copied
    recursively. Fields are deliberately NOT cloned: sampler/frame invalidation
    remains the caller's native binding responsibility. Unknown objects, custom
    classes and instance method overrides are not adapted. No cache is created.
    """
    try:
        if (not _native_graph(graph)
                or route.Corridor is not _NATIVE_CORRIDOR
                or fieldmod.Field is not _NATIVE_FIELD
                or type(graph.corr) is not _NATIVE_CORRIDOR
                or type(graph.corr.fld) is not _NATIVE_FIELD):
            return graph
        if native_routing_signature(graph, {"xz": (0.0, 0.0)}, 0.0) is not None:
            return graph
        if _MARKER in vars(graph):
            # Do not silently re-admit a changed snapshot's replacement arrays.
            return graph
        owned = {}
        arrays = {}

        def clone_attributes(source, *, corridor=False):
            values = dict(vars(source))
            if corridor:
                # Dataclass defaults can live on the class, not in __dict__.
                values.update((item.name, getattr(source, item.name))
                              for item in fields(_NATIVE_CORRIDOR))
            result = {}
            for name, value in values.items():
                if corridor and name == "fld":
                    result[name] = value
                elif type(value) is np.ndarray:
                    if (value.dtype.kind not in "biufc"
                            or value.dtype.metadata is not None
                            or value.dtype.fields is not None
                            or value.dtype.subdtype is not None):
                        raise TypeError("noncanonical routing array")
                    if id(value) not in owned:
                        owned[id(value)] = np.frombuffer(
                            value.tobytes(), dtype=value.dtype).reshape(value.shape)
                    result[name] = array = owned[id(value)]
                    arrays[(corridor, name)] = (weakref.ref(array), _layout(array))
                elif not corridor and name == "corr":
                    continue
                else:
                    result[name] = _plain(value, clone=True)
            return result

        graph_values = clone_attributes(graph)
        corr_values = clone_attributes(graph.corr, corridor=True)
        cloned = copy.copy(graph)
        cloned_corr = copy.copy(graph.corr)
        cloned.__dict__ = graph_values
        cloned_corr.__dict__ = corr_values
        cloned.corr = cloned_corr
        setattr(cloned, _MARKER, _Snapshot(object(), weakref.ref(cloned),
            weakref.ref(cloned_corr), weakref.ref(cloned_corr.fld),
            MappingProxyType(arrays)))
        return cloned
    except (AttributeError, TypeError, ValueError, RecursionError):
        return graph


def native_routing_signature(graph, bank, minimum_run):
    """Return an immutable O(metadata) token for an unchanged owned grid.

    This covers all graph/corr data bound by empty_bank_key, including graph.ok
    (the effective inverted forbid), labels, class, root, topology, geometry,
    cost/biome policy, bank and minimum run. It also binds currently overridden
    corr.forbid/forbid_all_water conservatively. Array replacement/layout edits
    or custom metadata opt out; changed plain metadata gets a different token.

    This token does NOT replace _route_catalog_binding: that function must still
    bind native Field owners, dispatch, helper code/defaults and physical policy.
    """
    try:
        state = vars(graph).get(_MARKER)
        if (type(state) is not _Snapshot or state.graph() is not graph
                or not _native_graph(graph)
                or route.Corridor is not _NATIVE_CORRIDOR
                or fieldmod.Field is not _NATIVE_FIELD
                or type(graph.corr) is not _NATIVE_CORRIDOR
                or type(graph.corr.fld) is not _NATIVE_FIELD
                or state.corr() is not graph.corr
                or state.frame() is not graph.corr.fld):
            return None
        tokens = []
        seen = set()
        for corridor, source in ((False, graph), (True, graph.corr)):
            values = dict(vars(source))
            if corridor:
                values.update((item.name, getattr(source, item.name))
                              for item in fields(_NATIVE_CORRIDOR))
            items = []
            for name, value in sorted(values.items()):
                if (not corridor and name in ("corr", _MARKER)
                        or corridor and name == "fld"):
                    continue
                key = (corridor, name)
                expected = state.arrays.get(key)
                if expected is not None:
                    if value is not expected[0]() or _layout(value) != expected[1]:
                        return None
                    seen.add(key)
                    # Snapshot identity binds contents; layout is checked above.
                    items.append((name, "owned-array"))
                else:
                    items.append((name, _plain(value)))
            tokens.append(tuple(items))
        if seen != state.arrays.keys():
            return None
        xz = bank["xz"]
        if type(xz) not in (list, tuple) or len(xz) != 2:
            return None
        coordinates = tuple(float(value) for value in xz)
        if not all(math.isfinite(value) for value in coordinates):
            return None
        policy = (_plain(route.GRADE_CEILING), _plain(route.BIOME_NAMES),
                  _plain(minimum_run), _plain(coordinates))
        methods = tuple((fn, fn.__code__) for fn in (
            route.Corridor._step_cost, route.Corridor.blocked,
            route.Corridor.nearest_allowed, route.Corridor.cell_of,
            route.Corridor.cell_world))
        return (state.identity, tuple(tokens), policy, methods)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError,
            RecursionError):
        return None
