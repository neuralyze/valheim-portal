"""Seed-scoped solved installation coordinates, without hydrating road nodes."""
from __future__ import annotations

import worldseed


# node id -> the `placements.yaml` record that SOLVED it.  `dockshore` is solved
# to the same centre by two presets (deepnorth-sandbox#sandbox-harbour and
# pre-elder#early-dock); either answers, and a disagreement between them is
# REFUSED below rather than resolved by whichever file was globbed first.
PLACEMENT_OF: dict[str, tuple[str, ...]] = {
    "portalhub": ("sandbox-portal-hub",),
    "meadhall": ("meadows-starter-hall",),
    "stathub": ("complete-station-hub",),
    "dockshore": ("sandbox-harbour", "early-dock"),
    "blacbase": ("blackforest-walled-base",),
    "plainsfm": ("plains-farm-base",),
    "workshop": ("iron-era-workshop",),
    "mtnpost": ("mountain-outpost",),
}

# A seed whose world has no placement set is REFUSED -- it has not sited its
# portal hall yet, and that is a missing prerequisite (placements), not a crossing
# the planet cannot have.


def installation_xz(seed: str | None = None,
                    world: str | None = None) -> dict[str, tuple[float, float]]:
    """Where this seed's 8 authored installations actually stand.

    Read from `worlds/<world>/*/placements.yaml` FOR THE SEED BEING PLANNED,
    through `worldseed.require_scope`, so a placement set solved on another seed
    is refused rather than transcribed, and refuses when this world has NO
    placement set at all -- because a road planned to a portal hall nobody has
    sited is the 59% defect wearing a different header.
    """
    import yaml                                             # noqa: PLC0415
    want_s = seed or worldseed.seed()
    want_w = world or worldseed.world()
    root, pattern, key = worldseed.installations_read(want_w)
    sets = sorted(root.glob(pattern)) if root.is_dir() else []
    found: dict[str, tuple[float, float, str]] = {}
    for path in sets:
        doc = yaml.safe_load(path.read_text())
        worldseed.require_scope(
            "installation placement set", path, found_seed=doc.get("seed"),
            found_world=doc.get("world"), seed_=want_s, world_=want_w,
            produced_by="blueprints/solve_placements.py, which solves this "
                        "world's buildings onto this seed's ground")
        for rec in doc.get("placements") or []:
            for nid, ids in PLACEMENT_OF.items():
                if rec.get("id") not in ids:
                    continue
                solved = rec.get(key) or {}
                if solved.get("x") is None or solved.get("z") is None:
                    continue
                xz = (float(solved["x"]), float(solved["z"]))
                prior = found.get(nid)
                if prior is not None and (prior[0], prior[1]) != xz:
                    raise SystemExit(
                        f"REFUSING: installation {nid!r} is solved to "
                        f"{(prior[0], prior[1])} in {prior[2]} and to {xz} in "
                        f"{path}. Two presets of the same world disagree about "
                        f"where one building stands, and a road cannot be "
                        f"planned to both.")
                found[nid] = (xz[0], xz[1], str(path))
    if found:
        missing = sorted(set(PLACEMENT_OF) - set(found))
        if missing:
            raise SystemExit(
                f"REFUSING: world {want_w!r} has a placement set for seed "
                f"{want_s!r} but it does not site {', '.join(missing)}. "
                f"{len(missing)} road node(s) would fall back to another "
                f"world's coordinates inside a plan the rest of which is this "
                f"seed's -- which is exactly the join of two planets this "
                f"module refuses everywhere else. Solve them with "
                f"blueprints/solve_placements.py for this world.")
        return {nid: (x, z) for nid, (x, z, _p) in found.items()}
    raise SystemExit(
        f"REFUSING: seed {want_s!r} has no sited installations. The 8 "
        f"installation and shoreline nodes are WHERE THIS PROJECT PUT ITS "
        f"BUILDINGS, and on seed {worldseed.DEFAULT_SEED!r} the "
        f"{len(PLACEMENT_OF)} of them carry 12 of the 40 EDGES and, MEASURED "
        f"on Fresh42, 59% of the planned metreage. "
        f"There is no frozen copy to fall back on: a frozen copy is "
        f"how a Fresh42 plan came to run 830.3 m from Ulfsland's portal hall "
        f"to Fresh42's own temple in one segment. Produce them with:\n"
        f"  blueprints/solve_placements.py for world {want_w!r} on seed "
        f"{want_s!r}")
