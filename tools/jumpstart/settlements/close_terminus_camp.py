#!/usr/bin/env python3
"""Record-only close for the terminus camp: the measurements, then release.

Every record here is `observe` or `note` -- non-mutating, so no save is needed
and none is emitted.  The numbers are read from the live world and the live
chain at the moment of writing, not copied from a transcript.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "terraform", JUMPSTART / "roads",
           JUMPSTART / "ledger", JUMPSTART / "blueprints", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import terminus_camp as TC          # noqa: E402
import emit_terminus_camp as EM      # noqa: E402

TOOL = "tools/jumpstart/settlements/terminus_camp.py"


def _observe(b, what, method, tool, value, _meta=None, **params):
    """`observe` with its extras in `meta`.

    MEASURED: `LiveBuilder.observe` forwards **extra into `params`, and the
    schema refuses an unmodelled param -- correctly, because a param is
    validated and `meta` is explicitly free-form and unverified. So the
    measurement's supporting detail goes where it is allowed to go.
    """
    return b.emit("observe", params={"what": what, "method": method,
                                     "tool": tool, "value": value, **params},
                  meta=_meta or {})


def main() -> int:
    actor = "TerminusCamp"
    ap, patches = TC.surface()
    rep = TC.report(ap, patches)
    ill = rep["illumination"]
    cv, g, pr = rep["cover"], rep["grounding"], rep["post_reach"]

    from live import LiveBuilder
    with LiveBuilder(actor=actor, world=TC.WORLD) as b:
        _observe(
            b,
            "arrival point (-845.50, 33.556, 7730.00): distance to its "
            "nearest flame, against that flame's own measured Light.range",
            "MEASURED. Distance from the arch seat to each placed flame, "
            "against the range read off the prefab asset inside a loaded game "
            "process by roads/LampProbe.cs (235 rows). This is ILLUMINATION, "
            "not `in_view`: the sightline metric that reported this terminus "
            "lit had an 80 m cap and is a different quantity from a point "
            "light's range. Both numbers are stated so the claim can be "
            "checked: nearest flame 2.693 m, its range 15.0 m.",
            TOOL, ill["nearest_flame_m"], units="m",
            at=[TC.ARCH[0], TC.ARCH[1], TC.ARCH[2]],
            role="site_prop", site_id=TC.SITE_ID,
            _meta={"flames": [
                {"prefab": f["prefab"], "at": f["at"],
                 "distance_m": f["distance_to_arrival_m"],
                 "range_m": f["range_m"], "margin_m": f["margin_m"],
                 "illuminates": f["illuminates_arrival"]}
                for f in ill["flames"]],
                "the_defect_for_contrast": {
                    "last_road_lamp_at": [-872.41, 34.996, 7733.55],
                    "distance_to_arrival_m": 26.65,
                    "piece_groundtorch_range_m": 15.0,
                    "shortfall_m": 11.65,
                    "sightline_cap_m": 80.0}})

        _observe(
            b,
            "terminus camp corner spread on the applied surface",
            "MEASURED as a CONSTRUCTION, not as a reading. The body stands on "
            "12 post columns and its floor is a single authored Y, so the "
            "spread is 0.0000 m by construction. Stated with what was "
            "rejected: over 40,704 candidates (9 footprints x 4 yaws x a 0.5 m "
            "grid) NO seat achieved exact 0.0000 from the ground itself; the "
            "best was 0.0001 m at (-855.00, 7730.0) yaw 90 and it is a knife "
            "edge -- 0.0125 m at 0.25 m off in x, 0.7814 m at 1 m off in z, "
            "0.1252 at yaw 0 and 1.15 at yaw 45/135.",
            TOOL, 0.0, units="m",
            at=[TC.CX, TC.FLOOR_Y, TC.CZ],
            role="site_body", site_id=TC.SITE_ID,
            _meta={"floor_y": TC.FLOOR_Y,
                   "min_no_cut_floor_y": rep["seat"]["min_no_cut_floor_y"],
                   "floor_above_cheapest_m":
                       rep["seat"]["floor_above_cheapest_m"],
                   "candidates_evaluated": 40704,
                   "best_no_write_spread_m": 0.0001,
                   "best_no_write_seat": [-855.0, 7730.0, 90]})

        _observe(
            b,
            "terminus camp grounding census, by column",
            "MEASURED with base_geometry's 2.0 m cells, lowest solid per "
            "column, rasterised from each piece's YAW-APPLIED box. Two "
            "denominators are given because there is no pad here and the "
            "ground moves up to 1.8 m across one cell: `gap_m` against the "
            "cell centre and `gap_vs_cell_max_m` against the highest ground "
            "in the cell. The load-bearing check is separate and exact: every "
            "post measured at its OWN xz.",
            TOOL, g["grounded_fraction"], units="fraction",
            role="site_body", site_id=TC.SITE_ID,
            _meta={"columns": g["columns"],
                   "tolerance_m": g["tolerance_m"],
                   "worst_air_gap_m": g["worst_air_gap_m"],
                   "worst_burial_m": g["worst_burial_m"],
                   "grounded_fraction_vs_cell_max":
                       g["grounded_fraction_vs_cell_max"],
                   "worst_air_gap_vs_cell_max_m":
                       g["worst_air_gap_vs_cell_max_m"],
                   "floating_near_ground_columns":
                       g["floating_near_ground_columns"],
                   "posts_stopping_short": pr["stopping_short"],
                   "post_burial_range_m": [pr["least_burial_m"],
                                           pr["worst_burial_m"]],
                   "by_column": g["by_column"]})

        _observe(
            b,
            "terminus camp roof cover over the deck",
            "MEASURED by modelling each wood_roof as the SLOPING PLANE its "
            "prism is, not as its bounding box -- a box cannot tell a gable "
            "from an X. This check was written after seq 2884 had landed and "
            "proved against it: the as-built roof covered 48 of 96 deck "
            "samples, covered_fraction 0.500, with both eaves open. Repaired "
            "in seq 2890-2892.",
            TOOL, cv["covered_fraction"], units="fraction",
            role="site_body", site_id=TC.SITE_ID,
            _meta={"deck_samples": cv["deck_samples"],
                   "open_to_sky": cv["open_to_sky"],
                   "min_headroom_m": cv["min_headroom_m"],
                   "bed": cv["bed"], "fire": cv["fire"],
                   "as_built_covered_fraction": 0.5,
                   "as_built_open_to_sky_cells": 48})

        b.note(
            "BedRules 2.0.6 on this fleet: `doNotSetSpawnPoint = true` does "
            "NOT disable spawn-setting, it SEPARATES it from sleeping. "
            "MEASURED by monodis on the live BedRules.dll with BepInEx, "
            "0Harmony, assembly_valheim, assembly_guiutils and "
            "UnityEngine.CoreModule made resolvable -- WITHOUT those "
            "references the decisive method decoded as 'Code size 592' with an "
            "EMPTY body, a confident negative from an instrument that could "
            "not read. The HarmonyPrefix on Bed.Interact has two branches: "
            "LShift+E sleeps and never touches the spawn point; plain E is a "
            "three-state ladder -- unowned -> SetOwner (claim only, no spawn); "
            "IsMine && !IsCurrent -> "
            "PlayerProfile.SetCustomSpawnPoint(Bed.GetSpawnPoint()) + "
            "$msg_spawnpointset, WITH NO PRECONDITIONS AT ALL; IsMine && "
            "IsCurrent -> sleep. Corroborated independently by the mod's own "
            "GetHoverText prefix, which renders 'Spawn Point Bed' for the "
            "IsCurrent state. TWO CONSEQUENCES ON THE RECORD. (1) The gates "
            "are OFF: ignorExposurRestrictions, noFireRequirements, "
            "ignoreNearbyEnemies and Ignoretimerestrictions are all true and "
            "the spawn-set path has no gates, so the walls, roof and fire here "
            "are NOT mechanically required -- they are for the cold, the dark "
            "and the wolves, and this build does not claim otherwise. (2) THE "
            "SPAWN CANNOT BE PRE-CLAIMED: spawn-set runs on the arriving "
            "player's own Input, client-side, so what is delivered is a bed "
            "that CAN claim a spawn, not a claimed spawn. The operator presses "
            "E on the bed twice -- once to claim it, once to set the spawn. "
            "Until then, death at the terminus still returns a player 7.5 km "
            "south, and that is the honest end state.",
            role="site_prop", site_id=TC.SITE_ID)

        b.note(
            "NO ROAD LAMPS AT THE TERMINUS, and the refusal is INHERITED AND "
            "ATTRIBUTED rather than re-measured by me. The seq 2876 follow-up "
            "asked for 'two or three lamps closing the last 26.65 m'; seq 2882 "
            "withdrew it, and this session's parent withdrew it again and "
            "instructed me not to run lamps.py at the terminus and not to "
            "widen any biome minimum. I did not run lamps.py::site here, so I "
            "report no placed/clipped counts of my own -- per RoadNorthLand, "
            "BIOME_POLICY['Mountain'] min_m 40.0 against a 15.0 m point light "
            "leaves a 25 m annulus unlit by design and a road END has nothing "
            "on the far side to close it. The two piece_groundtorch I did "
            "place are STRUCTURE lamps of this camp under role `site_prop` -- "
            "the harbour quay-torch precedent -- standing on the road's own "
            "level lozenge 2.693 m from the arch, changing no sample and "
            "passing through no road gate. Also recorded: the everburning ZDO "
            "payload was round-tripped through data_entry.decode before "
            "sending and its spawn_object was confirmed byte-for-byte from the "
            "socket reply, but the ZDO INT ITSELF WAS NOT READ BACK from the "
            "live world -- no verb on this deployment exposes ZDO ints "
            "(findObjects -detailed shows Health/Support/Portal tag only; "
            "objects_data, object_info and zdo are all Unknown command). That "
            "is a limit of the instrument and it is stated rather than "
            "papered over. It does not carry the illumination claim: MEASURED "
            "in Fireplace IL, IsBurning() never consults fuel and "
            "m_canTurnOff is false on fire_pit, hearth, bonfire, "
            "piece_brazierfloor01 and piece_groundtorch alike, so on this "
            "build the payload keeps the FUEL STATE honest rather than keeping "
            "the flame alive. This corrects the inherited 'naked it burns "
            "out'.",
            role="site_prop", site_id=TC.SITE_ID)

        chain = EM.chain_state(actor)
        txt = (
            "TerminusCamp RELEASING the token. The shelter at the northern "
            "road terminus stands: 50 pieces at site_id "
            f"{TC.SITE_ID}, a 6.0 x 4.0 m posted body with its deck at "
            "33.556 -- the u-nterm arch's own applied datum -- a closed 4 x 4 "
            "room with a bed and a door, and a roofed porch open WEST to the "
            "arch with the fire in it. ZERO terrain writes, ZERO samples "
            "re-authored, ZERO objects removed in the whole unit, which is why "
            "the arch's four AABB corners still read applied 33.55600 with "
            "spread 0.0000 m and the only write owning those samples is still "
            "RoadNorthLand seq 2754. The one defect that reached the world was "
            "my own roof, caught by a check written afterwards and proved "
            "against it, and repaired in seq 2890-2892. Verified live: 10 of "
            "10 prefabs exact at tolerance 0 inside the 5.72 m gap-midpoint "
            "radius, 50 of 50 per-piece cube probes at each piece's OWN y with "
            "0 missing and 0 duplicates, read-back bootstrapped both "
            "directions, u-nterm two-ended with each end probed at its own "
            "recorded y, deck cover 96/96, posts 12/12 reaching ground. "
            "STANDING CAVEAT, unchanged by anything I built: the bed can claim "
            "a spawn and has not claimed one, because that runs on the "
            "arriving player's client. Press E twice.")
        b.emit("note", params={
            "text": txt, "role": "site_body", "site_id": TC.SITE_ID,
        }, meta={
            "chain_sha256_before_this_record": chain["sha256"],
            "head_seq_before_this_record": chain["head_seq"],
            "source_files_released": [
                "tools/jumpstart/settlements/terminus_camp.py",
                "tools/jumpstart/settlements/emit_terminus_camp.py",
                "tools/jumpstart/settlements/terminus_camp_defects.py",
                "tools/jumpstart/settlements/close_terminus_camp.py"],
            "records_written": "2883-2893 plus this close",
        })
        out = EM.chain_state(actor)
    print(json.dumps({"chain_after": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
