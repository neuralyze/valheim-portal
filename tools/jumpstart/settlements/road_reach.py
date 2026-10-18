#!/usr/bin/env python3
"""A SITE IS WHERE A ROAD CAN GET TO, OR IT IS PORTAL-ONLY -- AND SAYS WHICH,
WITH NUMBERS.

THE DEFECT, MEASURED 2026-10-07 on Pirate68 with the 15 frozen site pins and
the 14 outlier anchors removed.  The solver ranks candidates by what a tower,
a light or a hamlet NEEDS (rise, relief, coast, purity) and never asked whether
a road can get there.  `sites.yaml` then carried 16 solver sites and the road
network, whose legs name them, routed 35 segments and came back with 7
UNROUTABLE:

    T10 T11 T12   `wt-spawn` (1432, 1546) stands in a 186-cell POCKET of the
                  router's own graph -- it is on the spawn landmass, and no
                  road gets in.
    S7            `wt-north` (1856, 146) is on a 62,618-cell island of that
                  graph; the spawn window's largest strong component is
                  269,270 cells.
    W9 W10 W11    `lh-west`, `tree-near`, `tree-west` -- the same island, each
                  hung from a west-isle node by a hand-typed leg.

The frozen pins had hidden it by being hand-picked routable places.  A site
that no road can reach is not a defect; an EDGE to it is.

WHO DECIDES WHETHER A SITE IS ROADED.  Two things, and they were never
joined:

  the DECLARATION   `OUTLIERS[...]["road_priority"]` (and `TOWNS[...]`):
                    `trunk` / `spur` is the carriageway class the site is to be
                    reached by, `none` is "portal-only, by design".  It was
                    authored, published in `sites.yaml`, and READ BY NOTHING:
                    `roads/spec.py` carried its own hand-typed legs.
  the MEASUREMENT   whether the router's own graph connects the site to the
                    trunk network.  Nobody asked.  This module asks, in the
                    router's own terms -- the SAME graph the extremities of
                    `roads/geography.py` are held to (`RoadGraph`), because "an
                    extremity is where a road can get to" and "a watchtower is
                    where a road can get to" are one rule.

THE VERDICT is `{class, roaded, reason, why, evidence}` and is carried on every
site `sites.yaml` publishes (`road`).  `roaded` is true only when the site's
declared class is a road class AND its window is rooted (a crossing joins it to
the spawn island on this seed) AND the router's snap for it lies in the
window's LARGEST STRONG COMPONENT of that class's graph AND on the window's
TRUNK LANDMASS (its largest land component: the router lets a road cross a
channel up to the span ceiling, which is how the two halves of the main
continent are one, and that is not a licence to bridge to every islet within
96 m of the coast).  A site that is not roaded is portal-only: no leg is planned to it (`roads/spec.py` records why, in
`NO_ROAD`, and never as an unroutable edge); where it stands on another
landmass it is a candidate for a dock or a ferry, later, and the numbers here
are what that decision starts from.

THE SOLVER USES IT TOO (`plan.select_outliers`): a site declared roaded takes
the best candidate of its kind that is reachable, and the candidates it passed
over are carried on the record with their numbers (`reach_refused`).  Only when
NO candidate of its scope is reachable does the site stand on the best
unreachable one -- never deleted -- and publish `roaded: false`.

ROAD_REACH_DEPENDS_ON
  the_routers_own_graph       `route.Corridor`'s achievable surface, span
                              ceiling, GRADE_CEILING and snap radius, as
                              `geography.RoadGraph`.
  the_trunk_landmass          the window's largest land component, as the
                              extremities of `geography.py` define it.
  the_generator_keep_outs     `poi.masks` over this seed's own dump, at the
                              class's half-width, as `network.py plan` masks it.
  the_class_the_site_declares a trunk site is judged on the trunk graph, a
                              spur site on the spur graph.
  a_rooted_window             the site's window holds an island a crossing
                              joins to the spawn island on this seed
                              (`geography.joined_islands`).
NEUTER `_MODE = "declared"`: the shipped reading -- a declared road class IS a
road, whatever the graph says.  Proven by `accept --section townsites`
(`a_pocket_the_router_cannot_enter_is_portal_only`,
`an_islet_across_the_water_is_portal_only`, `the_solver_passes_over_what_no_road_reaches`).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "roads", HERE.parent):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

ROAD_REACH_DEPENDS_ON = ("the_routers_own_graph", "the_trunk_landmass",
                         "the_generator_keep_outs",
                         "the_class_the_site_declares", "a_rooted_window")
_MODE = "graph"          # graph | declared (the shipped: a class IS a road)

# APPROACH_DEPENDS_ON: A SITE'S ROAD CAN ARRIVE ON ITS PAD, NOT ONLY ON ITS SNAP.
#
# The verdict above asks whether the router's graph reaches a cell 4 m from the
# site.  It never asks whether the LAST stretch -- from the stand-off ring onto
# the pad edge -- can be driven at the class's grade.  MEASURED on Pirate68
# regen #6: lh-south (pad datum 33.27 m on a sea-level shelf, ring ground
# 51-56 m on its land side, 29-30 m = under water elsewhere) was `reached` and
# the road ended 46.2 m short at 52.9 m, a 19.6 m drop in 28.6 m of run -- 69 %
# against a 12 % spur -- and the writer refused the batter with 89 rim steps of
# 3.3 m.  THE RULE: some LAND sample of the ring at the stand-off radius
# (pad half-diagonal + APPROACH_RING_M, MEASURED 30.6 m from that run's
# endpoint_standoff 46.2 - pad half-diagonal 15.6) must lie within
# grade * run + CUT_FILL_MAX of the pad datum.  A site with none is portal-only
# with the numbers (`approach_cliff`), and the solver passes over it exactly as
# it passes over a site no road reaches.
# NEUTER `_APPROACH_MODE = "snap"`: the shipped reading, snap only.
APPROACH_DEPENDS_ON = ("the_class_grade", "the_pad_datum", "the_ring_ground")
_APPROACH_MODE = "grade"        # grade | snap (the shipped)
APPROACH_RING_M = 30.6
APPROACH_BEARINGS = 16
LAND_MARGIN_M = 1.0             # a ring sample this far over the water plane


def approach_check(fld, x: float, z: float, pad_y: float, pad_m: float,
                   priority: str, water_y: float = 30.0) -> dict:
    """`{ok, best_gap_m, allowed_m, ring_m, land_samples}`: the smallest
    |ground - pad datum| over the ring's land samples against what the class
    grade allows over the run plus the cut/fill the writer may spend."""
    import math
    import geography as G                                   # noqa: PLC0415
    import route as routemod                                # noqa: PLC0415
    half_diag = pad_m * math.sqrt(2.0) / 2.0
    ring = half_diag + APPROACH_RING_M
    run = APPROACH_RING_M
    allowed = routemod.GRADE[priority] * run + routemod.CUT_FILL_MAX
    best, land = None, 0
    for k in range(APPROACH_BEARINGS):
        a = 2.0 * math.pi * k / APPROACH_BEARINGS
        try:
            h = G.sample_at(fld, x + ring * math.cos(a),
                            z + ring * math.sin(a))[0]
        except ValueError:                                  # off the window
            continue
        if h < water_y + LAND_MARGIN_M:
            continue
        land += 1
        gap = abs(h - pad_y)
        best = gap if best is None else min(best, gap)
    # NO LAND ON THE RING IS NOT A CLIFF: the road arrives by a bridge (lh-west
    # on Pirate68: ring wholly under water, W9's 42 m deck is its approach), and
    # whether a deck can land is the crossing rules' question, not this one's.
    return {"ok": best is None or best <= allowed,
            "best_gap_m": None if best is None else round(best, 2),
            "allowed_m": round(allowed, 2), "ring_m": round(ring, 1),
            "land_samples": land}


# The carriageway classes `roads/route.py::GRADE` knows.  `none` is the
# declaration that no road is planned to the site.
ROAD_CLASSES = ("trunk", "spur", "mountain")


class SiteReach:
    """The road-reach verdict for sites of ONE solve.

    `fields` are the loaded windows (`sites.Picker.fields`); `loc_path` is the
    generator location dump the solve reads, so the keep-outs the graph is
    built against are the ones the solve was judged on.  `joined` (default:
    `geography.joined_islands()`) and `graph_for` (default:
    `geography.RoadGraph`) are the two seams a fixture breaks, so a case need
    not read a 230 MB field to construct a pocket.
    """

    def __init__(self, fields: dict, loc_path=None, *, joined=None,
                 island_of=None, graph_for=None):
        import geography as G                               # noqa: PLC0415
        self.fields = fields
        self.loc_path = loc_path
        self._G = G
        self.joined = joined
        self.island_of = island_of or G.island_of_window
        self._graph_for = graph_for
        self._graphs: dict[tuple[str, str], object] = {}

    def _joined(self) -> set:
        if self.joined is None:
            self.joined = self._G.joined_islands()
        return self.joined

    def graph(self, patch: str, cls: str):
        key = (patch, cls)
        if key not in self._graphs:
            build = self._graph_for or (lambda f, c: self._G.RoadGraph(
                f, cls=c, loc=self.loc_path))
            self._graphs[key] = build(self.fields[patch], cls)
        return self._graphs[key]

    def verdict(self, patch: str, x: float, z: float, priority: str,
                pad_y: float | None = None, pad_m: float | None = None
                ) -> dict:
        """Judge independently in covering rooted windows; never join graphs.

        `window` remains the measurement patch. `topology_window` identifies the
        single window supplying the graph, landmass and approach evidence.
        """
        rec = {"class": priority, "window": patch,
               "xz": [round(float(x), 1), round(float(z), 1)]}
        if priority != "none" and priority not in ROAD_CLASSES:
            raise ValueError(
                f"road class {priority!r} is not one of "
                f"{('none',) + ROAD_CLASSES}; a class nothing reads is a "
                f"declaration that reads as 'no road' by accident")
        if priority == "none":
            return {**rec, "roaded": False, "reason": "declared_portal_only",
                    "why": ("road_priority is none: this site is reached by "
                            "portal, by design, and no road is planned to it"),
                    "evidence": {}}
        if _MODE == "declared":
            return {**rec, "roaded": True, "reason": "declared",
                    "why": f"road_priority {priority}: taken as a road",
                    "evidence": {}}
        joined = self._joined()
        windows = [pid for pid, fld in self.fields.items()
                   if self.island_of(pid) in joined and fld.contains(x, z)]
        windows.sort(key=lambda pid: (pid != patch, pid))
        if not windows:
            return {**rec, "roaded": False, "reason": "unrooted_window",
                    "topology_window": None,
                    "why": (f"no rooted window covers ({x:.0f}, {z:.0f}); "
                            f"measurement window {patch!r} is not evidence "
                            f"of a separate island or of road connectivity"),
                    "evidence": {"island": self.island_of(patch),
                                 "joined_islands": sorted(joined),
                                 "covering_rooted_windows": []}}
        attempts = []
        first = None
        for pid in windows:
            verdict = self._window_verdict(
                pid, x, z, priority, pad_y=pad_y, pad_m=pad_m)
            attempts.append({"window": pid, "reason": verdict["reason"],
                             "roaded": verdict["roaded"],
                             "evidence": verdict["evidence"]})
            if first is None:
                first = verdict
            if verdict["roaded"]:
                break
        else:
            verdict = first
        evidence = {**verdict["evidence"],
                    "island": self.island_of(verdict["window"]),
                    "joined_islands": sorted(joined)}
        if len(attempts) > 1:
            evidence["topology_attempts"] = attempts
        return {**verdict, **rec, "topology_window": verdict["window"],
                "evidence": evidence}

    def _window_verdict(self, patch: str, x: float, z: float, priority: str,
                        pad_y: float | None = None,
                        pad_m: float | None = None) -> dict:
        """All constraints must hold in this one rooted, covering window."""
        rec = {"class": priority, "window": patch,
               "xz": [round(float(x), 1), round(float(z), 1)]}
        g = self.graph(patch, priority)
        at = g.at([x], [z])
        piece, snap = int(at["piece_cells"][0]), float(at["snap_m"][0])
        land = int(at["land_cells"][0])
        ev = {"cell_m": g.corr.cell_m, "network_cells": g.largest_cells,
              "piece_cells": piece, "land_cells": land,
              "trunk_land_cells": g.trunk_land_cells,
              "snap_m": None if snap == float("inf") else round(snap, 1),
              "snap_limit_m": g.snap_m}
        if (bool(at["reached"][0]) and bool(at["on_trunk_landmass"][0])
                and _APPROACH_MODE == "grade" and pad_y is not None
                and pad_m is not None):
            ap = approach_check(self.fields[patch], x, z, pad_y, pad_m,
                                priority)
            ev["approach"] = ap
            if not ap["ok"]:
                return {**rec, "roaded": False, "reason": "approach_cliff",
                        "why": (f"the {priority} router reaches a cell "
                                f"{ev['snap_m']:g} m away, but the nearest "
                                f"land at the {ap['ring_m']:g} m stand-off "
                                f"ring is {ap['best_gap_m']} m from the pad "
                                f"datum {pad_y:.2f} against "
                                f"{ap['allowed_m']} m a {priority} road can "
                                f"close over {APPROACH_RING_M:g} m "
                                f"({ap['land_samples']} land samples): the "
                                f"road cannot arrive on the pad "
                                f"(APPROACH_DEPENDS_ON)"),
                        "evidence": ev}
        if bool(at["reached"][0]) and bool(at["on_trunk_landmass"][0]):
            return {**rec, "roaded": True, "reason": "reached",
                    "why": (f"the {priority} router snap stands "
                            f"{ev['snap_m']:g} m away in window {patch!r}'s "
                            f"largest strong component "
                            f"({ev['network_cells']} cells of "
                            f"{ev['cell_m']:g} m), on its trunk landmass "
                            f"({ev['trunk_land_cells']} cells)"),
                    "evidence": ev}
        if not bool(at["inside"][0]):
            reason, why = "outside_grid", (
                f"({x:.0f}, {z:.0f}) is outside window {patch!r}'s corridor "
                f"grid, so the router cannot start a search there")
        elif piece == 0:
            reason, why = "no_allowed_cell", (
                f"no allowed cell of the {priority} router graph lies within "
                f"{g.snap_m:g} m of ({x:.0f}, {z:.0f}) (nearest "
                f"{ev['snap_m']} m): every cell there is wet or inside a "
                f"keep-out")
        elif not bool(at["on_trunk_landmass"][0]):
            reason, why = "off_landmass", (
                f"({x:.0f}, {z:.0f}) stands on a landmass of {land} cells of "
                f"{ev['cell_m']:g} m, and window {patch!r}'s trunk landmass "
                f"holds {ev['trunk_land_cells']}: a road does not cross open "
                f"water to another landmass -- a dock, a ferry or a portal "
                f"does")
        else:
            reason, why = "off_network", (
                f"the {priority} router snap for ({x:.0f}, {z:.0f}) stands in "
                f"a component of {piece} cells of {ev['cell_m']:g} m, and "
                f"window {patch!r}'s largest strong component holds "
                f"{ev['network_cells']}: no road reaches it")
        return {**rec, "roaded": False, "reason": reason, "why": why,
                "evidence": ev}


def brief(v: dict) -> str:
    """One refusal line for a candidate the solver passed over."""
    return (f"({v['xz'][0]:.0f}, {v['xz'][1]:.0f}) on {v['window']}: "
            f"{v['reason']} -- {v['why']}")
