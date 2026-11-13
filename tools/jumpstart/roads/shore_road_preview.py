"""Local measured shore approaches through the unmodified native road constructor.

This is not named-road, writer, census or saved-world acceptance. In particular,
source-pad preflight stops at its measured exterior handoff; it neither reads a
world ledger nor guesses the pad's eventual applied height.
"""
from __future__ import annotations

import copy
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

from crossing_contract import BANK_LEVEL_M, transverse_window
from crossing_physical import CoverageError
from arrival_geometry import fixed_landing_rejection


def _plain(value):
    """Publish only normal YAML/JSON scalars, not NumPy profile scalars."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


class _MeasuredField:
    """Native frame/arrays, but every physical height comes from the real surface."""

    def __init__(self, field, surface):
        self._field = field
        self._surface = surface
        self.coverage_failure = None

    def __getattr__(self, name):
        return getattr(self._field, name)

    def _unread(self, message):
        self.coverage_failure = CoverageError(message)
        raise self.coverage_failure

    def bilinear(self, x, z):
        height = float(self._surface.at(float(x), float(z))[0])
        if not math.isfinite(height):
            self._unread(f"unread native shore approach at ({x}, {z})")
        return height

    def contains(self, x, z, margin=0.0):
        # The surface can dispatch to another measured inset. Its coverage,
        # not Field.bilinear's clamped rectangular fallback, is authoritative.
        self.bilinear(x, z)
        if margin:
            for dx, dz in ((-margin, -margin), (-margin, margin),
                           (margin, -margin), (margin, margin)):
                self.bilinear(x + dx, z + dz)
        return True

    def sample_many(self, xs, zs):
        points = np.column_stack((np.asarray(xs).ravel(), np.asarray(zs).ravel()))
        batch = getattr(self._surface, "sample_points", None)
        heights = (np.asarray(batch(points), dtype=np.float64) if batch is not None
                   else np.array([self.bilinear(x, z) for x, z in points]))
        if heights.shape != (len(points),):
            raise ValueError("shore sample_points must return one height per point")
        if not np.isfinite(heights).all():
            self._unread("unread batched native shore approach footprint")
        heights = heights.reshape(np.shape(xs))
        return heights, np.ones(heights.shape, dtype=bool)


class NativeShorePreview:
    """One lazy private native kernel per solve/derive/produce scope.

    Large immutable field/corridor arrays are borrowed, never deep-copied. Only
    small semantic node/context dictionaries and a candidate mask are owned.
    The object is sequential, like the solve scope that owns it.
    """

    def __init__(self, *, native_writer=False):
        self._kernel = None
        self._spec = None
        self._native_writer = native_writer
        self._native_patches = {}

    def _prepare(self):
        if self._kernel is not None:
            return self._kernel
        path = Path(__file__).with_name("network.py")
        module_spec = importlib.util.spec_from_file_location(
            "_shore_native_network", path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"native shore constructor unavailable: {path}")
        kernel = importlib.util.module_from_spec(module_spec)
        kernel._tool_data_import = True
        # network's ordinary import path setup must not accumulate per kernel.
        old_path = sys.path[:]
        try:
            module_spec.loader.exec_module(kernel)
        finally:
            sys.path[:] = old_path
        self._spec = kernel.specmod
        kernel.specmod = SimpleNamespace(**vars(self._spec))
        self._kernel = kernel
        return kernel

    def __call__(self, *, graph, surf, source_id, source_node, terminal_id,
                 root, bearing, width_m, level_y, source_approach=None):
        old_path = sys.path[:]
        try:
            return self._evaluate(
                graph=graph, surf=surf, source_id=source_id,
                source_node=source_node, terminal_id=terminal_id,
                root=root, bearing=bearing, width_m=width_m, level_y=level_y,
                source_approach=source_approach)
        finally:
            sys.path[:] = old_path

    def _evaluate(self, *, graph, surf, source_id, source_node, terminal_id,
                  root, bearing, width_m, level_y, source_approach=None):
        kernel = self._prepare()
        matches = [edge for edge in self._spec.TERMINAL_EDGES
                   if edge["a"] == source_id and edge["b"] == terminal_id]
        if len(matches) != 1:
            raise ValueError(f"expected one canonical serving edge for "
                             f"{source_id!r} -> {terminal_id!r}; got {len(matches)}")
        edge = copy.deepcopy(matches[0])
        if float(edge.get("width_m", kernel.WIDTH_M[edge["cls"]])) != float(width_m):
            raise ValueError("shore width disagrees with canonical serving edge")
        root = tuple(map(float, root))
        angle = math.radians(float(bearing))
        entry = (root[0] - BANK_LEVEL_M * math.sin(angle),
                 root[1] - BANK_LEVEL_M * math.cos(angle))
        source = copy.deepcopy(source_node)
        source.setdefault("kind", "extremity")
        source.setdefault("patch", graph.corr.fld.id)
        source.setdefault("island", source["patch"])
        edge["patch"] = graph.corr.fld.id
        source_level = source.get("level_y")
        goal = tuple(map(float, source["xz"]))
        if source_approach is not None:
            goal = tuple(map(float, source_approach["handoff_xz"]))
            # Preserve the semantic node/centre. Its pad does not own an
            # invented datum at the exterior measured road endpoint.
            source.pop("level_y", None)
            source_level = None
        terminal = dict(xz=root, kind="waterfront_terminal", owner="Crossings",
                        level_y=float(level_y), island=source["island"],
                        patch=source["patch"], jetty_bearing_deg=float(bearing),
                        arrive_bearing_deg=(float(bearing) + 180.0) % 360.0)
        nodes = {source_id: source, terminal_id: terminal}
        # A real declared source abutment keeps its exact opposite-bank owner.
        declared = [copy.deepcopy(c) for c in self._spec.DECLARED_CROSSINGS
                    if c["crossing_id"] == source.get("crossing")]
        for crossing in declared:
            for side in ("bank_a", "bank_b"):
                node_id = crossing[side]["node"]
                if node_id not in nodes:
                    nodes[node_id] = copy.deepcopy(self._spec.NODES[node_id])
        kernel.specmod.NODES = nodes
        kernel.specmod.DECLARED_CROSSINGS = declared
        # Every candidate replaces ALL native mutable solve context. Never
        # inherit a previous root, far access anchor, pad claim or datum.
        kernel._NODE_ANCHORS = {source_id: goal} if source_approach is not None else {}
        kernel._ARRIVAL_STARTS = {}
        kernel._PAD_CLAIMS = []
        kernel._PAD_DATUMS = {}
        kernel._PLAN_PAD_KEEPOUTS = None
        kernel._PLAN_PATCHES = kernel.writer_patches if self._native_writer else None
        kernel._PLAN_KEEPOUTS = kernel.writer_keepouts if self._native_writer else None
        kernel._GEN_PATCHES = self._native_patches if self._native_writer else {}
        field = _MeasuredField(graph.corr.fld, surf)
        corridor = copy.copy(graph.corr)
        corridor.forbid = np.logical_not(graph.ok)
        if graph.corr.forbid is not None:
            corridor.forbid |= graph.corr.forbid
        corridor.last_terminal = None
        # Keep the caller's guidance, POI/physical masks and native water/span
        # model. Only the native constructor decides whether water is bridged.
        landing, _ = kernel.routemod.resample([root, entry], 1.0)
        low, high, off = transverse_window(field, landing, float(width_m))
        if off:
            raise CoverageError("unread fixed native shore landing footprint")
        if (np.any(low < kernel.WATER_LEVEL)
                or np.any(np.maximum(abs(low - level_y), abs(high - level_y))
                          > kernel.routemod.CUT_FILL_MAX)
                or not kernel._bank_path_allowed(corridor, [root, entry], None)):
            return None
        try:
            if source_approach is not None or (
                    source.get("kind") == "abutment" and source.get("crossing")):
                expected_source = np.asarray(goal, dtype=float)
            else:
                origin = kernel.search_xz(
                    source_id, corridor, width_m=float(width_m), fld=field)
                expected_source = np.asarray(
                    corridor.cell_world(*corridor.nearest_allowed(*origin)),
                    dtype=float)
        except ValueError:
            return None
        def build(**kwargs):
            old_path = sys.path[:]
            try:
                joint = {}
                record = kernel.build_segment(
                    field, edge, corridor, [], census=None, joint=joint, **kwargs)
                return record, joint
            finally:
                sys.path[:] = old_path

        def rejection(record):
            if field.coverage_failure is not None:
                raise field.coverage_failure
            why = kernel.plan_refusal(record, [], kernel.SEED, fld=field, coverage=False)
            if why is not None:
                return why
            if (record["residual_infeasible"] or record["poi_hard_violations"]
                    or any(not bank["within_clamp"] for bank in record["abutments"])
                    or any(not bank["single_H_both_banks"] for bank in record["bank_level"])):
                return "physical_profile"
            points = np.asarray(record["nodes"], dtype=float)
            # Output xz is rounded to centimetres, not a new attachment.
            tolerance = math.sqrt(2.0) * 0.005 + 1e-8
            if fixed_landing_rejection(reversed(points), root, bearing, BANK_LEVEL_M) is not None:
                return "fixed_landing"
            if not any(bank["node"] == terminal_id and bank["level_y"] == float(level_y)
                       and bank["level_run_m"] == BANK_LEVEL_M
                       for bank in record["abutments"]):
                return "held_landing"
            if np.linalg.norm(points[0] - expected_source) > tolerance:
                return "source_attachment"
            return None

        try:
            record, joint = build()
            if self._native_writer:
                # Reuse native continuation before discarding an owned root.
                # Missing integer measurements remain prerequisites.
                old_path = sys.path[:]
                try:
                    record, joint = kernel.adjacency_reroute(
                        corridor, lambda guidance: build(extra_guidance=guidance),
                        initial=(record, joint))
                    if rejection(record) is not None:
                        return None
                    record, joint = kernel.water_route(
                        field, corridor, record, joint,
                        build=build, refusal=rejection)
                    if field.coverage_failure is not None:
                        raise field.coverage_failure
                finally:
                    sys.path[:] = old_path
                if kernel.final_verdict(record["writer_water"]) != "CLEAR":
                    return None
        except CoverageError:
            raise
        except ValueError:
            if field.coverage_failure is not None:
                raise field.coverage_failure
            return None
        if field.coverage_failure is not None:
            raise field.coverage_failure
        if rejection(record) is not None:
            return None
        points = np.asarray(record["nodes"], dtype=float)
        record["preflight_scope"] = {
            "kind": "local measured approach preflight",
            "source_node": copy.deepcopy(source_node),
            "terminal_node": terminal,
            "source_handoff": copy.deepcopy(source_approach),
            "authority_pending": ["full named road", "source pad applied datum/arrival",
                                  "native writer", "census", "saved world"],
            "pad_context": "current graph masks; no world ledger or applied pad datum read"}
        return _plain(dict(length_m=record["length_m"], grade_max=record["grade_max_measured"],
                    source_level_y=source_level, arrival_level_y=float(level_y),
                    stations=len(points), nodes=record["nodes"], width_m=float(width_m),
                    entry_xz=list(entry), source_snap_m=math.dist(points[0], goal),
                    water_reroute_rounds=len(record.get("writer_water", {}).get("tried", [])),
                    routing={"mode": "native_mixed", "refusal": None,
                             "scope": "local measured approach preflight"},
                           native_approach=record))
