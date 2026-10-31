"""Process-local COMPLETE routing enumerations, never compiled-side recipes.

Integration (after candidate's mandatory canonical span coverage checks)::

    def signature():
        coarse = empty_bank_key(graph, corr, bank, minimum_run)
        return None if coarse is None else (canonical_policy_token(), coarse, datum)
    binding = lambda: native_frame_binding((*self.fields, graph.corr.fld),
                                           policy_token=canonical_policy_token())
    yield from catalog.routes(signature=signature(), current_signature=signature,
        binding=binding,
        producer=lambda route_stats: uncached_routes(graph, bank, route_stats),
        stats=public_stats)

The producer receives a NEW routing-only stats dictionary. It must not receive
_side_options' public stats: compilation mutates those while routing is suspended.
Every route must still pass _compile_side for this crossing/other bank. Do not
cache compiler refusals, legal-plan counts, construction coverage or side plans.

The immutable, hashable signature must cover ALL coarse routing inputs (including
empty_bank_key's corridor/masks/labels/bank/minimum run), datum, goal ordering,
and the native method/code/policy token. That token must cover dispatched sampler,
route generator, routed_approach, certificate and graph methods; simplify/resample,
straighten/transverse_window/footprint offsets and their constants; water level,
widths, bank run, station spacing, freeboard, cut/fill, batter grade/reach, water
stand-off and edge-cost/search policy. Include callable AND __code__ identity when
runtime code replacement is possible. Noncanonical overrides should opt out.
Pass current_signature when coarse data can change during suspension, and compute
the policy token afresh in binding, rather than closing over its initial value.
The binding validates native frame identity, not the caller's policy semantics.

Stats are cumulative producer snapshots, merged per yielded route and at normal
completion, never added to themselves. Optional coverage counts/errors retain
unknown/missing/None values exactly; zero is never invented for coverage. On replay
route_queries/base_search_expansions are zero ACTUAL work, not logical evidence.
Exclude those and route_catalog_reuses from candidate's geometric bank evidence.
Cache limits affect admission/eviction ONLY: routing always exhausts uncapped.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Iterator, MutableMapping
import copy
from dataclasses import dataclass, field
import math
import pickle
import sys

import numpy as np

import field as fieldmod

_NATIVE_AT = fieldmod.Field.at
_NATIVE_INDEX = fieldmod.Field.index
_NATIVE_AT_CODE = _NATIVE_AT.__code__
_NATIVE_INDEX_CODE = _NATIVE_INDEX.__code__


@dataclass(frozen=True)
class NativeFrameBinding:
    """Identity plus strong owners, preventing object-id recycling while cached."""

    fingerprint: tuple
    owners: tuple = field(compare=False, repr=False)
    root_ids: tuple[int, ...]


def _array_identity(array, dtype, n):
    if (type(array) is not np.ndarray or array.dtype != np.dtype(dtype)
            or array.dtype.metadata is not None
            or array.shape != (n, n) or array.strides != (n * array.itemsize, array.itemsize)
            or array.flags.writeable or not array.flags.c_contiguous):
        return None
    owners, identities, seen = [], [], set()
    owner = array
    while True:
        if id(owner) in seen:
            return None
        seen.add(id(owner))
        owners.append(owner)
        if type(owner) is bytes:
            identities.append((id(owner), len(owner)))
            break
        if type(owner) is np.ndarray:
            if owner.flags.writeable or owner.dtype.hasobject or owner.base is None:
                return None
            identities.append((id(owner), owner.dtype.str, owner.shape, owner.strides,
                               owner.__array_interface__["data"][0]))
            owner = owner.base
        elif type(owner) is memoryview:
            if not owner.readonly:
                return None
            identities.append((id(owner), owner.format, owner.shape, owner.strides,
                               owner.nbytes, owner.readonly))
            owner = owner.obj
        else:
            # Includes mmap, bytearray, writable owners hidden by readonly views,
            # foreign buffers and ndarray subclasses with custom semantics.
            return None
    return tuple(identities), tuple(owners)


def native_frame_binding(fields: Iterable, *, policy_token: Hashable = ()):
    """Return exact native immutable frame binding, otherwise disable reuse.

    Only exact field.Field/ndarray types qualify. Both h and biome must have the
    native little-endian float32/uint8 square layout and readonly ownership chains
    terminating in immutable bytes. Metadata, array/base/data identities, offsets,
    strides, coordinate frame, order and policy participate in invalidation.
    No field contents are copied or hashed. Synthetic writable arrays (even behind
    readonly views), mmap and mutable bytes owners deliberately do not qualify.
    Include graph.corr.fld as well as physical sampler fields: water stand-off
    reads that coarse field's h/frame even though empty_bank_key omits fld.
    Coarse frames may have non-unit positive step and non-integer origins.
    """
    try:
        hash(policy_token)
        fields = tuple(fields)
        if not fields:
            return None
        fingerprints, owners = [], [fields, policy_token]
        for frame in fields:
            if (type(frame) is not fieldmod.Field or type(frame.id) is not str
                    or type(frame.n) is not int or frame.n < 2
                    or getattr(frame.at, "__func__", None) is not _NATIVE_AT
                    or getattr(frame.index, "__func__", None) is not _NATIVE_INDEX
                    or _NATIVE_AT.__code__ is not _NATIVE_AT_CODE
                    or _NATIVE_INDEX.__code__ is not _NATIVE_INDEX_CODE):
                return None
            coordinates = (frame.cx, frame.cz, frame.half, frame.step)
            if (not all(type(v) in (int, float) and math.isfinite(v) for v in coordinates)
                    or frame.half <= 0 or frame.step <= 0
                    or not math.isfinite(frame.x0) or not math.isfinite(frame.z0)):
                return None
            height = _array_identity(frame.h, "<f4", frame.n)
            biome = _array_identity(frame.biome, "u1", frame.n)
            if height is None or biome is None:
                return None
            fingerprints.append((id(frame), frame.id, *coordinates, frame.n,
                                 frame.x0, frame.z0, height[0], biome[0]))
            owners.extend((frame, *height[1], *biome[1]))
        roots = tuple(sorted({id(owner) for owner in owners if type(owner) is bytes}))
        return NativeFrameBinding((policy_token, tuple(fingerprints)), tuple(owners), roots)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


class _Uncacheable(ValueError):
    pass


def _value_size(value, seen=None):
    """Conservative admission estimate before making a serialization copy."""
    if seen is None:
        seen = set()
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if type(value) is np.ndarray:
        if value.dtype.hasobject:
            raise _Uncacheable("object arrays are not route geometry")
        return size + value.nbytes
    if isinstance(value, np.generic):
        if value.dtype.hasobject:
            raise _Uncacheable("object scalar")
        return size
    if type(value) in (type(None), bool, int, float, str, bytes):
        return size
    if type(value) in (tuple, list):
        return size + sum(_value_size(item, seen) for item in value)
    if type(value) is dict:
        return size + sum(_value_size(k, seen) + _value_size(v, seen)
                          for k, v in value.items())
    raise _Uncacheable("route records/stats must contain plain data")


def _encode(value, available):
    if _value_size(value) > available:
        raise _Uncacheable("admission byte budget")
    result = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    if sys.getsizeof(result) > available:
        raise _Uncacheable("serialized admission byte budget")
    return result


def _signature_size(value, seen=None):
    # Tokens may contain functions/code objects; charge their references/shallow
    # objects, not the external module globals reached through those functions.
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if type(value) in (tuple, frozenset):
        size += sum(_signature_size(item, seen) for item in value)
    return size


@dataclass(frozen=True)
class _Entry:
    # Private immutable bytes: no retained mutable producer/caller geometry.
    steps: tuple[tuple[bytes, bytes], ...]
    final_stats: bytes
    size: int
    binding: NativeFrameBinding


class RouteCatalog:
    """Bounded LRU of complete enumerations; cancellation never publishes state.

    Single-process, single-owner generator use, like the existing physical context.
    Limits count copied payloads/container overhead, not borrowed native field bytes
    or policy functions. Exactly one current native root-owner SET is borrowed;
    replacing that set or opting out drops all entries/old snapshot owners.
    Same-root frame/policy variants have separate exact keys and may coexist.
    """

    def __init__(self, *, max_entries=64, max_bytes=64 * 1024 * 1024):
        if type(max_entries) is not int or type(max_bytes) is not int:
            raise TypeError("catalog bounds must be integers")
        if max_entries < 0 or max_bytes < 0:
            raise ValueError("catalog bounds must be nonnegative")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self._pending_bytes = 0
        self._binding = None

    @property
    def entry_count(self):
        return len(self._entries)

    @property
    def byte_count(self):
        return self._bytes

    @property
    def pending_byte_count(self):
        return self._pending_bytes

    def clear(self):
        self._entries.clear()
        self._bytes = 0
        self._binding = None

    def _activate(self, binding):
        if (binding is None or self._binding is None
                or binding.root_ids != self._binding.root_ids):
            self.clear()
        self._binding = binding

    def _reserve(self, amount):
        while self._entries and self._bytes + self._pending_bytes + amount > self.max_bytes:
            _key, old = self._entries.popitem(last=False)
            self._bytes -= old.size
        if self._bytes + self._pending_bytes + amount > self.max_bytes:
            return False
        self._pending_bytes += amount
        return True

    @staticmethod
    def _merge(stats, routing_stats, *, replay=False):
        stats.update(routing_stats if replay else copy.deepcopy(routing_stats))
        if replay:
            stats["route_queries"] = 0
            stats["base_search_expansions"] = 0
            stats["route_catalog_reuses"] = 1

    def routes(self, *, signature: Hashable, binding: Callable,
               producer: Callable[[dict], Iterator], stats: MutableMapping,
               current_signature: Callable | None = None):
        """Yield fresh routes, recording only an unchanged, normally exhausted run.

        Producer-yielded None means an omitted route (never sent to side compiler).
        Intrinsic refusal/optional-coverage data belongs in its local route_stats.
        Exceptions propagate unchanged. close()/throw(), changed binding/signature,
        unsupported record data and admission overflow discard pending state.
        Even after admission fails, the producer continues with unchanged physics.
        """
        initial = binding()
        self._activate(initial)
        try:
            hash(signature)
            cacheable = signature is not None and initial is not None
        except TypeError:
            cacheable = False

        def unchanged():
            latest = binding()
            self._activate(latest)
            return (latest == initial and initial is not None
                    and (current_signature is None or current_signature() == signature))

        key = (initial.fingerprint, signature) if cacheable else None
        entry = self._entries.get(key) if cacheable else None
        if entry is not None and unchanged():
            self._entries.move_to_end(key)
            for route_bytes, stats_bytes in entry.steps:
                if not unchanged():
                    # Already replayed records cannot be replaced by a fresh run:
                    # never splice old and new terrain enumeration together.
                    self.clear()
                    raise RuntimeError("routing inputs changed during catalog replay")
                self._merge(stats, pickle.loads(stats_bytes), replay=True)
                yield pickle.loads(route_bytes)
            if not unchanged():
                self.clear()
                raise RuntimeError("routing inputs changed during catalog replay")
            self._merge(stats, pickle.loads(entry.final_stats), replay=True)
            return

        route_stats = {}
        steps = []
        used = _signature_size(key) + sys.getsizeof(steps) + 256
        recording = (cacheable and self.max_entries > 0 and used < self.max_bytes
                     and self._reserve(used))
        reserved = used if recording else 0
        iterator = None
        try:
            iterator = iter(producer(route_stats))
            for record in iterator:
                if recording and not unchanged():
                    recording = False
                    steps.clear()
                    initial = None
                    self._pending_bytes -= reserved
                    reserved = 0
                if record is not None:
                    if recording:
                        try:
                            route_bytes = _encode(record, self.max_bytes - used)
                            stats_bytes = _encode(route_stats, self.max_bytes - used
                                                  - sys.getsizeof(route_bytes))
                            cost = (sys.getsizeof(route_bytes) + sys.getsizeof(stats_bytes)
                                    + sys.getsizeof((route_bytes, stats_bytes)) + 16)
                            if used + cost > self.max_bytes or not self._reserve(cost):
                                raise _Uncacheable("entry admission byte budget")
                            reserved += cost
                            steps.append((route_bytes, stats_bytes))
                            used += cost
                        except (ValueError, TypeError, OverflowError, pickle.PickleError, MemoryError):
                            recording = False
                            steps.clear()
                            self._pending_bytes -= reserved
                            reserved = 0
                        finally:
                            route_bytes = stats_bytes = None
                    # Never expose the routing-only dict to the compiler via
                    # nested certificate/coverage values in public evidence.
                    self._merge(stats, route_stats)
                    yield record
            # A producer's close error is still a failed enumeration, so close
            # before publishing. Cancellation/exception closes in finally below.
            close = getattr(iterator, "close", None)
            iterator = None
            if close is not None:
                close()
            self._merge(stats, route_stats)
            if recording and unchanged() and self._binding == initial:
                try:
                    final_stats = _encode(route_stats, self.max_bytes - used)
                    cost = sys.getsizeof(final_stats) + sys.getsizeof(tuple(steps))
                    if used + cost > self.max_bytes or not self._reserve(cost):
                        raise _Uncacheable("final admission byte budget")
                    used += cost
                    reserved += cost
                except (ValueError, TypeError, OverflowError, pickle.PickleError, MemoryError):
                    return
                previous = self._entries.pop(key, None)
                if previous is not None:
                    self._bytes -= previous.size
                self._pending_bytes -= reserved
                reserved = 0
                while self._entries and len(self._entries) >= self.max_entries:
                    _key, old = self._entries.popitem(last=False)
                    self._bytes -= old.size
                self._entries[key] = _Entry(tuple(steps), final_stats, used, initial)
                self._bytes += used
        finally:
            self._pending_bytes -= reserved
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
