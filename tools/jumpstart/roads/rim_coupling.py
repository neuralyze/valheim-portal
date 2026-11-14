"""Bounded local terrain continuation; a failed attempt is not impossibility.

The caller owns all geometry, bands, edge binding and refusal semantics. This
module only solves interval/difference constraints and expands an implicated
mutable boundary. It never treats a sequential solver's visited set as a pin.
"""
from collections import deque
import math


def neighbours(cell):
    x, z = cell
    return ((x - 1, z), (x, z - 1), (x, z + 1), (x + 1, z))


def continue_component(seed, *, height, band, binds, limit_m, validate,
                       max_cells=256):
    """Return a validated local assignment, or leave the caller untouched.

    ``band(c)`` is None for a real pin, otherwise the exact allowed interval.
    A boundary is initially fixed at its current height. Only a mutable
    boundary responsible for a contradictory bound is admitted next. Every
    constraint is an actual binding cardinal edge incident to the component.
    ``validate`` must check native refusals and the whole proposed boundary;
    on rejection this function makes no physical-feasibility claim.
    """
    component = {seed}
    bands = {}

    def bounds(c):
        if c not in bands:
            bands[c] = band(c)
        return bands[c]

    if bounds(seed) is None:
        return {"status": "pinned", "cells": (), "assignment": {}}
    while len(component) <= max_cells:
        domain = set(component)
        for c in sorted(component):
            domain.update(n for n in neighbours(c) if binds(c, n))
        lower, upper, lower_from, upper_from = {}, {}, {}, {}
        for c in sorted(domain):
            interval = bounds(c) if c in component else None
            if interval is None:
                h = height(c)
                if h is None or not math.isfinite(h):
                    return {"status": "unreadable", "cells": tuple(sorted(component)),
                            "assignment": {}}
                interval = (h, h)
            lower[c], upper[c] = interval
            lower_from[c] = upper_from[c] = c
        adjacency = {c: tuple(n for n in neighbours(c) if n in domain
                             and (c in component or n in component)
                             and binds(c, n)) for c in sorted(domain)}

        def propagate(starts):
            queue = deque(sorted(starts))
            queued = set(queue)
            while queue:
                c = queue.popleft()
                queued.discard(c)
                if lower[c] > upper[c] + 1e-10:
                    return c
                for n in adjacency[c]:
                    changed = False
                    if lower[c] - limit_m > lower[n] + 1e-10:
                        lower[n] = lower[c] - limit_m
                        lower_from[n] = lower_from[c]
                        changed = True
                    if upper[c] + limit_m < upper[n] - 1e-10:
                        upper[n] = upper[c] + limit_m
                        upper_from[n] = upper_from[c]
                        changed = True
                    if changed and n not in queued:
                        queue.append(n)
                        queued.add(n)
            return None

        bad = propagate(domain)
        if bad is not None:
            expand = {c for c in (lower_from[bad], upper_from[bad])
                      if c not in component and bounds(c) is not None}
            if not expand:
                return {"status": "blocked", "cells": tuple(sorted(component)),
                        "assignment": {}}
            if len(component | expand) > max_cells:
                break
            component.update(expand)
            continue
        # Each propagated interval is its exact projection for these difference
        # constraints. Fix a height near the existing surface, then propagate
        # before choosing the next one; independent per-cell clamping is not a
        # coherent assignment. Sorted choices make the continuation repeatable.
        for c in sorted(component):
            target = min(max(height(c), lower[c]), upper[c])
            lower[c] = upper[c] = target
            if propagate((c,)) is not None:
                return {"status": "numeric", "cells": tuple(sorted(component)),
                        "assignment": {}}
        assignment = {c: lower[c] for c in sorted(component)}
        if not validate(assignment):
            return {"status": "rejected", "cells": tuple(sorted(component)),
                    "assignment": {}}
        return {"status": "clear", "cells": tuple(sorted(component)),
                "assignment": assignment}
    return {"status": "bounded", "cells": tuple(sorted(component)),
            "assignment": {}}
