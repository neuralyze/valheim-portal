"""Import-safe survey scope shared by the request producer and shore solver."""
from dataclasses import replace

SEARCH_RADIUS_M = 512.0
FOOTPRINT_MARGIN_M = 128.0


def request_windows(windows):
    """Scan complete shore searches around later generated trunk anchors.

    No field, installation, settlement or road-spec import belongs in stage 4.
    The ordinary combined scan budget applies after these requests are added.
    """
    margin = SEARCH_RADIUS_M + FOOTPRINT_MARGIN_M
    # Keep routing/geography windows unchanged. Expanding their geometry would
    # move extremities to the new boundary and lose the promised search margin.
    # These are extra measured patches, not aliases for a smaller old request.
    return tuple(windows) + tuple(
        replace(w, fid="shore-" + w.fid, half=w.half + margin, legacy="",
                role=w.role + "; waterfront search and hull envelope")
        for w in windows if w.fid in ("trunk-primary", "trunk-secondary"))


def connectivity_window(windows, world_radius_m):
    """One component-complete rendered world, shared by every ferry candidate."""
    primary = next(w for w in windows if w.fid == "trunk-primary")
    half = float(world_radius_m) + FOOTPRINT_MARGIN_M + 0.5
    return replace(primary, fid="shore-connectivity-world", cx=0.0, cz=0.0,
        half=half, step=1.0, legacy="",
        role="complete rendered playable-world land identity; no cropped/coarse component proof")


def ferry_search(windows, complete, spawn_xz, min_area_m2):
    """Second request phase: actual complete rendered topology nominates shores.

    These small windows cover road approach, all local shore alternatives and
    hulls. The complete world remains the sole component identity authority.
    """
    from shore_connectivity import candidate_landings
    primary = next(w for w in windows if w.fid == "trunk-primary")
    candidates = candidate_landings(complete, spawn_xz, min_area_m2)
    requests = []
    for candidate in candidates:
        x, z = candidate["probe_xz"]
        fid = f"ferry-shore-{candidate['rendered_component']}"
        requests.append(replace(primary, fid=fid, cx=x, cz=z,
            half=SEARCH_RADIUS_M+FOOTPRINT_MARGIN_M+0.5, step=1.0, legacy="",
            role=f"all measured shore/road/hull candidates of rendered component {candidate['rendered_component']}"))
        candidate["search_patch"] = fid
    return tuple(requests), candidates
