#!/usr/bin/env python3
"""Does this body SIT on a flat pad -- and if not, is that a defect or a design?

The operator's rule, verbatim: *"blueprints that are mostly flat are best unless
they are for building over water.. then the bases are often uneven"*. That is not
one test, it is two questions, and conflating them is how a harbour gets rejected
for being a harbour:

    1. How much of the footprint stands in AIR on a flat pad?
    2. Is that air BETWEEN SUPPORTS -- piles under a pier, posts under a
       balcony, arches under a bridge -- or is it a whole wing with nothing
       under it, held up by a hillside that does not exist here?

Question 1 is `base_geometry.BaseProfile.air`, already written and already
measured. Question 2 is what this module adds, and it is answerable from the
same raster: for every column of the footprint that floats, how far is the
nearest column that reaches the ground?

    air_isolation_max_m   max over floating columns of the distance to the
                          nearest GROUNDED column, in metres.

That number is what separates a deck on posts from a wing on nothing, and the
MEASURED pair that proves it is worth stating, because the two bodies have
almost the SAME air fraction and opposite structure:

    drake-lighthouse      33.3% floating, isolation  2.00 m  -> a tower on legs
    BjOrN_blueprint001    34.1% floating, isolation 33.29 m  -> needs a hillside

Over the 528 resolvable bodies the two populations barely touch: bodies whose
worst column is within 6 m of support run 2.00-6.00 m (n=102, median 4.00),
and the rest run 6.32-100.58 m (n=99, median 16.49). MEASURED.

So `setting` is a three-way GEOMETRIC verdict, and it says nothing about intent:

    land_flat        grounds as captured: air within the allowed fraction
    low_plinth       air present, but its WORST gap is under one wall course --
                     a worktop or a step, i.e. furniture rather than a building
    supported_air    air present but never isolated -- piles, posts, arches,
                     overhangs. The air is under a deck the player walks ON
    needs_support    air AND isolation. Something has to be under that area and
                     a flat pad is not it: terrain, water, or a gorge

**`needs_support` is deliberately not called `needs_terrain`.** Geometry cannot
tell a harbour from a hillside castle -- MEASURED, Olivanderr's `dock` is 70.1%
floating with a 13.42 m isolation, which is numerically indistinguishable from a
slope capture, because a pier's open basin IS a large unsupported span. The
discriminator is the body's PURPOSE, which is a curated or derived `category`
and not a measurement. So intent is reported as a SEPARATE field with its source
named:

    intended_setting    over_water when `category` is dock or bridge, else land
                        -- DERIVED FROM CATEGORY, never from geometry
    flat_pad_verdict    grounds | grounds_on_posts
                        | DEFECT_on_land   needs_support and meant for land
                        | expected_over_water  needs_support and meant for water

`DEFECT_on_land` is the only verdict that condemns a body, and it is the
operator's rule stated exactly: uneven base, land site.

Usage:
    ./audit_bases.py                  # table to stdout
    ./audit_bases.py --json OUT       # machine-readable, one object per body
    ./audit_bases.py --shortlist      # the per-preset ranking
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
# `blueprints/` and `library/` BOTH contain a `materialise.py`, so import order
# decides which one `import materialise` reaches. The library's own directory
# goes last so it wins, and the blueprints modules are imported by their
# unambiguous names first.
sys.path.insert(0, str(JUMPSTART / "blueprints"))

import base_geometry as bg  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402

sys.path.insert(0, str(HERE))
for _shadowed in ("materialise",):
    sys.modules.pop(_shadowed, None)

from classify import BEDS, PORTALS, STATIONS, TIER_ORDER, load_evidence  # noqa: E402
from materialise import load_rows, locate  # noqa: E402

# How far a floating column may be from the nearest grounded column before the
# air under it stops reading as "between supports" and starts reading as "this
# wing has nothing under it".
#
# MEASURED, over the 528 resolvable bodies: the distribution of isolation is
# strongly bimodal. Bodies whose air is structural -- the docks, the twelve
# bridges, the stilt swamp town, the portal arcades -- sit at 2.00-6.00 m
# (n=102, median 4.00), which is Valheim's own beam/pole module repeated.
# Bodies captured on slopes and cliffs run 6.32-100.58 m (n=99, median 16.49).
# 6.0 m sits in that gap: it admits a 4 m pile grid with a diagonal (5.66 m) and
# rejects anything with a column more than three 2 m cells from support.
ISOLATION_TOL_M = 6.0

# Below this, the body's worst gap is not "in the air" at all.
#
# 1.0 m is one wall course -- the same bound `base_geometry.MAJOR_LEVEL_CLIMB_M`
# is set from -- so a body whose WORST column stands less than a course clear
# has no room for a storey under it. What it has is a worktop, a plinth or a
# step, which is what furniture looks like to a raster: the datum lands on the
# station standing on the floor and the countertop above it reads as "clear".
#
# MEASURED, and the reason this exists: without this gate exactly 1 body of 528
# was condemned by it -- `hs_blackforest_kitchenbench_brigames`, a 110-piece
# kitchen unit, 86.7% "floating" with a worst gap of 0.77 m. The 10th percentile
# of worst-gap among the other 89 condemned bodies is 4.62 m, so the gate
# separates exactly that one case and touches nothing else. One wrong verdict in
# a table a human acts on is worth removing; a gate wide enough to remove two
# would not be.
PLINTH_TOL_M = 1.0

# Prefab-name patterns for the pieces that make air LEGITIMATE: the things a
# builder puts under a deck when there is water or a gap beneath it. Counted,
# never used to decide the verdict -- the verdict is geometry. This is the
# corroborating evidence a reader wants beside it.
PILE_HINTS = ("wood_pole", "wood_beam", "woodiron_beam", "woodiron_pole",
              "stone_pole", "darkwood_beam", "wood_log", "iron_grate",
              "blackmarble_column", "dvergr_pole")


def isolation(prof: bg.BaseProfile, base_y: float,
              tol: float = bg.BASE_PROFILE_FLAT_TOL_M) -> dict:
    """How far the air under this body is from the nearest thing holding it up.

    Both figures are in metres over the same 2 m raster `base_profile` builds:

        max_m    the worst column -- the one a player would fall through
        mean_m   the average over floating columns

    Computed as a true nearest-neighbour search over the grounded set rather
    than a neighbourhood test, because a 466 m2 floating deck has no grounded
    cell in ANY fixed neighbourhood and a fixed-radius test would report the
    same "no support nearby" for a 5 m overhang and for a whole floating wing.
    """
    if not prof.bottom:
        return {"columns_floating": 0, "max_m": 0.0, "mean_m": 0.0}
    floating = []
    grounded = []
    for (ix, iz), y in prof.bottom.items():
        (floating if y - base_y > tol else grounded).append((ix, iz))
    if not floating:
        return {"columns_floating": 0, "max_m": 0.0, "mean_m": 0.0}
    if not grounded:
        # Nothing in the whole body reaches the pad. A floating island, or a
        # bridge deck whose piers are outside the raster.
        return {"columns_floating": len(floating), "max_m": float("inf"),
                "mean_m": float("inf")}
    cell = prof.cell_m
    dists = []
    for fx, fz in floating:
        best = min((fx - gx) ** 2 + (fz - gz) ** 2 for gx, gz in grounded)
        dists.append(math.sqrt(best) * cell)
    return {
        "columns_floating": len(floating),
        "max_m": round(max(dists), 2),
        "mean_m": round(sum(dists) / len(dists), 2),
    }


def setting(air: dict, iso: dict) -> tuple[str, str]:
    """(geometric verdict, how it was determined).

    Purely a function of the two measured numbers above -- no name list, no
    curated judgement, nothing INFERRED. It says what is under the body and
    NOT what the builder meant; see `intent()` for that half.
    """
    if not air or air["floating_fraction"] <= bg.BASE_PROFILE_AIR_AREA_FRACTION:
        return "land_flat", (
            f"MEASURED: {air.get('floating_fraction', 0.0):.1%} of the footprint "
            f"stands clear, within the {bg.BASE_PROFILE_AIR_AREA_FRACTION:.0%} allowed"
        )
    if air["max_air_m"] < PLINTH_TOL_M:
        return "low_plinth", (
            f"MEASURED: {air['floating_fraction']:.1%} of the footprint stands clear, "
            f"but the WORST gap in the whole body is {air['max_air_m']:.2f} m -- under "
            f"one wall course ({PLINTH_TOL_M:g} m). Nothing is in the air: this is a "
            f"worktop, a plinth or a step, and it is furniture or a fitting rather than "
            f"a building with a foundation"
        )
    if iso["max_m"] <= ISOLATION_TOL_M:
        return "supported_air", (
            f"MEASURED: {air['floating_fraction']:.1%} of the footprint stands clear, "
            f"but no floating column is more than {iso['max_m']:.2f} m from one that "
            f"reaches the pad (tolerance {ISOLATION_TOL_M:g} m), so the air is BETWEEN "
            f"supports -- piles, posts, arches or an overhang"
        )
    return "needs_support", (
        f"MEASURED: {air['floating_fraction']:.1%} of the footprint stands clear and "
        f"its worst column is {iso['max_m']:.2f} m from anything that reaches the pad, "
        f"over the {ISOLATION_TOL_M:g} m tolerance -- nothing IN THE BODY holds that "
        f"area up, so something outside it must: terrain, water or a gorge"
    )


# Categories whose whole purpose is to stand over water or over a gap. A body in
# one of these with an uneven base is doing its job; a body outside them with
# an uneven base is the defect the operator saw on the pad.
#
# This is the ONE place intent enters the audit, and it is derived from
# `category`, which is a curated verdict for the 90 hand-catalogued web rows and
# the 87 corpus rows, and a DERIVED one (from station/portal/bed counts) for the
# 362 bulk rows. Geometry cannot supply it: MEASURED, Olivanderr's `dock` is
# 70.1% floating with 13.42 m of isolation, which is arithmetically the same
# shape as a hillside castle. Pretending otherwise would be a check that
# confidently answers a question it is not measuring.
OVER_WATER_CATEGORIES = frozenset(("dock", "bridge"))


def intent(category: str, metadata: str, verdict: str) -> tuple[str, str, str]:
    """(intended_setting, source of that claim, flat-pad verdict)."""
    over_water = category in OVER_WATER_CATEGORIES
    intended = "over_water" if over_water else "land"
    source = (f"DERIVED FROM CATEGORY '{category}' ({metadata} metadata) -- not from "
              f"geometry, which cannot distinguish a harbour basin from a hillside")
    if verdict in ("land_flat", "low_plinth"):
        return intended, source, "grounds"
    if verdict == "supported_air":
        return intended, source, "grounds_on_posts"
    return intended, source, ("expected_over_water" if over_water else "DEFECT_on_land")


def audit() -> list[dict]:
    geom = bg.geometry()
    vanilla, mod_owner = load_evidence()
    rows: list[dict] = []
    for entry in load_rows():
        row = {
            "name": entry["name"],
            "source_name": entry.get("source_name", entry["name"]),
            "origin": entry["origin"],
            "licence": entry["licence"],
            # `redistributable` is a statement about the SOURCE's licence, not
            # about this repo's choice: `web.py`'s `redistributable` field is
            # what decides committing. Recorded here so the audit table can be
            # read without cross-referencing the manifest.
            "redistributable": entry["licence"] != "none stated",
            "category": entry["category"],
            # "tier(count)" in the manifest; split so the ranking can weigh the
            # COUNT. MEASURED reason that matters: `PuP_forge1` reads
            # `ashlands(2)` for two ashwood trim pieces on a 343-piece wood
            # workshop, and `SettlerRuins_Ashlands1` reads `meadows` despite
            # being Ashlands-themed because its Ashlands content is world
            # statics rather than craftable material. A tier label without its
            # count answers neither case correctly.
            "top_tier": entry["top_tier"].split("(")[0],
            "top_tier_pieces": int(entry["top_tier"].split("(")[1].rstrip(")")),
            "kind": entry["kind"],
            "metadata": entry.get("metadata", "curated"),
            "verdict": entry["verdict"],
            "sha256": entry["sha256"],
        }
        if not entry["resolvable"]:
            rows.append({**row, "setting": "unresolvable",
                         "flat_pad_verdict": "no_body",
                         "intended_setting": "unknown",
                         "setting_evidence": entry["unresolved_reason"]})
            continue
        path = locate(entry)
        if path is None:
            rows.append({**row, "setting": "unresolvable",
                         "flat_pad_verdict": "no_body",
                         "intended_setting": "unknown",
                         "setting_evidence": "no body hashes to the manifest's sha256"})
            continue
        try:
            objs = read_objects(path)
        except Exception as exc:
            rows.append({**row, "setting": "parser_refusal",
                         "flat_pad_verdict": "unmeasurable",
                         "intended_setting": "unknown",
                         "setting_evidence": f"{type(exc).__name__}: {exc}"})
            continue

        datum = bg.floor_datum(objs, geom)
        prof = datum.profile
        fp = bg.xz_footprint(objs, 0.0, geom)
        counts = Counter(o.prefab for o in objs)
        missing = sorted(
            p for p in counts
            if p not in vanilla and p not in mod_owner
        )
        air = prof.air(datum.base_y) if (prof and datum.base_y is not None) else {}
        iso = (isolation(prof, datum.base_y)
               if (prof and datum.base_y is not None) else
               {"columns_floating": 0, "max_m": 0.0, "mean_m": 0.0})
        verdict, evidence = setting(air, iso)
        intended, intent_source, pad_verdict = intent(
            entry["category"], row["metadata"], verdict)
        rows.append({
            **row,
            "pieces": len(objs),
            "distinct_prefabs": len(counts),
            "footprint_x_m": round(fp.size_x, 1),
            "footprint_z_m": round(fp.size_z, 1),
            "footprint_span_m": round(max(fp.size_x, fp.size_z), 1),
            "base_y": None if datum.base_y is None else round(datum.base_y, 3),
            "datum_method": datum.method,
            "relief_m": round(prof.relief_m, 3) if prof else None,
            "columns": prof.columns if prof else 0,
            "footprint_area_m2": round(prof.area_m2, 1) if prof else 0.0,
            "floating_fraction": air.get("floating_fraction", 0.0),
            "floating_area_m2": air.get("floating_area_m2", 0.0),
            "mean_air_m": air.get("mean_air_m", 0.0),
            "max_air_m": air.get("max_air_m", 0.0),
            "air_volume_m3": air.get("air_volume_m3", 0.0),
            "max_buried_m": air.get("max_buried_m", 0.0),
            "air_isolation_max_m": iso["max_m"],
            "air_isolation_mean_m": iso["mean_m"],
            "pile_pieces": sum(n for p, n in counts.items()
                               if any(h in p.lower() for h in PILE_HINTS)),
            "stations": sum(counts[s] for s in STATIONS if s in counts),
            "station_kinds": sorted(s for s in STATIONS if s in counts),
            "portals": sum(counts[p] for p in PORTALS if p in counts),
            "beds": sum(counts[b] for b in BEDS if b in counts),
            "missing_prefabs": missing,
            "missing_pieces": sum(counts[p] for p in missing),
            "walkable_levels": len(datum.levels),
            "setting": verdict,
            "setting_evidence": evidence,
            "intended_setting": intended,
            "intended_setting_source": intent_source,
            "flat_pad_verdict": pad_verdict,
        })
    return rows


TSV_COLUMNS = (
    "name", "flat_pad_verdict", "setting", "intended_setting", "floating_fraction",
    "air_isolation_max_m", "mean_air_m", "max_air_m", "relief_m", "footprint_span_m",
    "footprint_area_m2", "pieces", "stations", "portals", "beds", "missing_pieces",
    "verdict", "category", "kind", "metadata", "licence", "origin",
)


def tsv(rows: list[dict]) -> str:
    out = ["\t".join(TSV_COLUMNS)]
    for r in rows:
        out.append("\t".join(
            str(r.get(c, "")).replace("\t", " ").replace("\n", " ") for c in TSV_COLUMNS
        ))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# the per-preset shortlist
#
# Nine presets, each with a pad and a station set. `pad_m` and `biome` are
# MEASURED out of worlds/Ulfsland/<preset>/placements.yaml (`requirement.
# footprint_m` and `requirement.biome`); `wants` is the station list that
# preset's own placement note and worlds/tier-policy.yaml call for, and
# `forbids` is tier bleed -- a station the party has not earned, which
# `to_rcon_plan.py --drop-prefab` can strip but which costs a rank.
# ---------------------------------------------------------------------------

# A station GROUP is a base prefab plus its extensions, named exactly.
#
# Exact names, not substrings, and the reason is a bug this had for one run:
# `"forge" in "blackforge"` is True, so a substring test credited every
# Mistlands black forge as satisfying a bronze-age forge requirement AND
# reported `forge` as tier bleed on `pre-kall` for bodies that carry only a
# black forge. `forge_ext1`..`forge_ext6` and `blackforge_ext*` collide the same
# way. Every name below is a member of `classify.STATIONS`, which is itself a
# list of names CONFIRMED to occur in a real body rather than guessed.
STATION_GROUPS: dict[str, frozenset[str]] = {
    "workbench": frozenset(("piece_workbench", "piece_workbench_ext1",
                            "piece_workbench_ext2", "piece_workbench_ext3",
                            "piece_workbench_ext4")),
    "forge": frozenset(("forge", "forge_ext1", "forge_ext2", "forge_ext3",
                        "forge_ext4", "forge_ext5", "forge_ext6")),
    "blackforge": frozenset(("blackforge", "blackforge_ext1", "blackforge_ext2_vise",
                             "blackforge_ext3_metalcutter", "blackforge_ext4_gemcutter")),
    "smelter": frozenset(("smelter",)),
    "charcoal_kiln": frozenset(("charcoal_kiln",)),
    "blastfurnace": frozenset(("blastfurnace",)),
    "windmill": frozenset(("windmill",)),
    "spinningwheel": frozenset(("piece_spinningwheel",)),
    "eitrrefinery": frozenset(("eitrrefinery",)),
    "galdr_table": frozenset(("piece_magetable", "piece_magetable_ext",
                              "piece_magetable_ext2", "piece_magetable_ext3")),
    "artisan": frozenset(("piece_artisanstation", "artisan_ext1")),
    "stonecutter": frozenset(("piece_stonecutter",)),
    "cauldron": frozenset(("piece_cauldron", "cauldron_ext1_spice",
                           "cauldron_ext3_butchertable", "cauldron_ext4_pots",
                           "cauldron_ext5_mortarandpestle", "cauldron_ext6_rollingpins",
                           "piece_MeadCauldron")),
    "cookingstation": frozenset(("piece_cookingstation", "piece_cookingstation_iron")),
    "oven": frozenset(("piece_oven",)),
    "preptable": frozenset(("piece_preptable",)),
    "beehive": frozenset(("piece_beehive",)),
    # Present in `classify.STATIONS` because they are crafting-station objects,
    # but no preset ranks on them: a barber, a bathtub and a sap collector are
    # amenities, not progression gates. Listed so the coverage check below can
    # be exhaustive -- a station nobody can ask for is a station nobody can rank
    # on, and silently omitting three of them would hide that.
    "amenity": frozenset(("piece_barber", "piece_bathtub", "piece_sapcollector")),
}

_UNGROUPED = set(STATIONS) - {n for g in STATION_GROUPS.values() for n in g}
if _UNGROUPED:  # a station nobody can ask for is a station nobody can rank on
    raise SystemExit(
        "STATION_GROUPS does not cover classify.STATIONS: " + ", ".join(sorted(_UNGROUPED))
    )

PRESETS: dict[str, dict] = {
    "pre-eikthyr": {
        "wants_bed": True,
        "pad_m": 24, "biome": "Meadows",
        "wants": ["workbench", "cookingstation"],
        "forbids": ["forge", "blackforge", "smelter", "blastfurnace", "artisan",
                    "eitrrefinery", "galdr_table", "windmill", "spinningwheel"],
        "why": "tier 1. A forge here hands out a whole tier the party has not reached. "
               "Chests are not ranked on: containers are stocked by stock.py, not "
               "inherited from the body.",
    },
    "pre-elder": {
        "pad_m": 56, "biome": "BlackForest",
        "wants": ["forge", "workbench", "cauldron", "cookingstation", "smelter",
                  "charcoal_kiln"],
        "forbids": ["blastfurnace", "blackforge", "artisan", "eitrrefinery",
                    "galdr_table", "windmill", "spinningwheel"],
        "why": "tier 2. Bronze age: forge and smelter yes, artisan table no.",
    },
    "pre-bonemass": {
        "pad_m": 70, "biome": "BlackForest",
        "wants": ["forge", "smelter", "charcoal_kiln", "stonecutter", "cauldron",
                  "workbench"],
        "forbids": ["blastfurnace", "blackforge", "artisan", "eitrrefinery",
                    "galdr_table", "windmill"],
        "why": "tier 3. Iron workshop. The artisan table needs a Dragon Tear, so it "
               "leaks a tier -- which is exactly why halvar-master-refinery was "
               "previously rejected on theme grounds.",
    },
    "pre-moder": {
        "wants_bed": True,
        "pad_m": 40, "biome": "Mountain",
        "wants": ["forge", "workbench", "cookingstation", "oven", "cauldron"],
        "forbids": ["blackforge", "eitrrefinery", "galdr_table"],
        "why": "tier 4. Mountain outpost; a 40 m pad is the binding constraint.",
    },
    "pre-yagluth": {
        "pad_m": 60, "biome": "Plains",
        "wants": ["windmill", "blastfurnace", "artisan", "forge", "smelter", "oven",
                  "spinningwheel"],
        "forbids": ["blackforge", "eitrrefinery", "galdr_table"],
        "why": "tier 5. Plains industry: windmill and blast furnace are the signature.",
    },
    "pre-queen": {
        "pad_m": 80, "biome": "Mistlands",
        "wants": ["blackforge", "blastfurnace", "galdr_table", "artisan",
                  "spinningwheel", "eitrrefinery"],
        "forbids": [],
        "why": "tier 6. Everything is earned by now; the black forge is the marker.",
    },
    "pre-fader": {
        "pad_m": 50, "biome": "AshLands",
        "wants": ["blackforge", "eitrrefinery", "galdr_table", "artisan"],
        "forbids": [],
        "why": "tier 7. Ashlands materials; grausten and flametal read as the setting.",
    },
    "pre-kall": {
        "wants_bed": True,
        "pad_m": 32, "biome": "DeepNorth",
        "wants": ["cauldron", "cookingstation"],
        "forbids": ["forge", "blackforge", "blastfurnace", "artisan", "smelter"],
        "why": "tier 8, but the SITE is a landing camp, not a base -- deliberately no "
               "forge. A bed matters here and is ranked as `beds` rather than as a "
               "station, because it is not one.",
    },
    "deepnorth-sandbox": {
        "pad_m": 56, "biome": "Meadows",
        "wants": ["workbench", "forge", "stonecutter", "artisan", "blackforge",
                  "blastfurnace", "smelter", "charcoal_kiln", "windmill",
                  "spinningwheel", "eitrrefinery", "galdr_table", "oven",
                  "preptable", "cauldron", "cookingstation"],
        "forbids": [],
        "why": "tier 9 sandbox: the more stations the better, nothing is off-limits.",
    },
}

# A pad is square and the body is placed centred on it, so a body FITS when its
# longer horizontal span is inside the pad. No margin is added: `solve_placements`
# already flattens the full `footprint_m` square and `xz_footprint` measures the
# occupied box rather than the pivot box, so the two numbers are comparable
# directly.


def matches(row: dict, preset: dict) -> dict:
    kinds = set(row.get("station_kinds") or [])
    have = sorted(w for w in preset["wants"] if kinds & STATION_GROUPS[w])
    bleed = sorted(f for f in preset["forbids"] if kinds & STATION_GROUPS[f])
    return {"wants_met": have, "wants_missing": sorted(set(preset["wants"]) - set(have)),
            "tier_bleed": bleed}


# The material tier each preset's party has actually reached, as an index into
# `classify.TIER_ORDER` (meadows, blackforest, swamp, mountain, plains,
# mistlands, ashlands). `pre-bonemass` is the iron era, so swamp; `pre-kall` and
# `deepnorth-sandbox` sit past the last material tier and cap at ashlands.
PRESET_TIER_INDEX = {
    "pre-eikthyr": 0, "pre-elder": 1, "pre-bonemass": 2, "pre-moder": 3,
    "pre-yagluth": 4, "pre-queen": 5, "pre-fader": 6, "pre-kall": 6,
    "deepnorth-sandbox": 6,
}

# How many pieces of over-tier MATERIAL make a body wrong for a tier rather than
# merely trimmed with it.
#
# A station can be stripped -- `to_rcon_plan.py --drop-prefab` removes it and
# the building still stands. A WALL cannot: strip 928 grausten pieces out of
# `hs_ashlands_cm_thug_outpost` and there is no building left. So material
# over-tier ranks ABOVE station bleed, and the threshold exists because the
# label alone lies in both directions: MEASURED, `PuP_forge1` reads
# `ashlands(2)` for two trim pieces on a 343-piece wood workshop, and
# `SettlerRuins_Ashlands1` reads `meadows` while being Ashlands-themed because
# its Ashlands content is world statics. 20 pieces is above the trim cases seen
# in the corpus (2, 4, 14, 16) and far below any real structural use.
OVER_TIER_PIECE_FLOOR = 20


def rank(rows: list[dict], preset_name: str) -> list[dict]:
    """Bodies that would GROUND, FIT and carry the right stations, best first.

    Ordering, in strict precedence:

      1. it grounds on a flat pad, then it grounds on posts, then everything
         else -- because the operator's complaint was that the building floats,
         and no amount of station coverage fixes that;
      2. its MATERIALS are within the tier, by more than a trim allowance --
         a station can be stripped, a wall cannot;
      3. every station the preset wants;
      4. no station it forbids (`to_rcon_plan.py --drop-prefab` CAN strip one,
         so bleed costs a rank rather than disqualifying);
      5. nothing unspawnable;
      6. a bed, where the preset asks for one;
      7. least air;
      8. fewest pieces -- at one RCON `spawn` per object a 900-piece body is a
         900-command placement and a 30,000-piece one is an outage.

    Bodies whose span exceeds the pad are dropped outright, as are exploit
    payloads, the 0-byte rows and Infinity Hammer's parser fixtures.
    """
    preset = PRESETS[preset_name]
    budget = PRESET_TIER_INDEX[preset_name]
    out = []
    for r in rows:
        if r["setting"] in ("unresolvable", "parser_refusal"):
            continue
        if r["kind"] in ("exploit", "empty"):
            continue
        if r["category"] == "fixture":
            continue
        if r.get("footprint_span_m", 1e9) > preset["pad_m"]:
            continue
        m = matches(r, preset)
        over = TIER_ORDER.index(r["top_tier"]) - budget
        material_bleed = (over > 0 and r["top_tier_pieces"] >= OVER_TIER_PIECE_FLOOR)
        out.append({
            **r, **m,
            "wants_met_n": len(m["wants_met"]),
            "material_tiers_over": max(over, 0),
            "material_bleed": material_bleed,
            "material_bleed_pieces": r["top_tier_pieces"] if material_bleed else 0,
        })
    wants_bed = preset.get("wants_bed", False)
    out.sort(key=lambda r: (
        r["flat_pad_verdict"] != "grounds",
        r["flat_pad_verdict"] != "grounds_on_posts",
        r["material_bleed"],
        -r["wants_met_n"],
        len(r["tier_bleed"]),
        r["missing_pieces"] > 0,
        -min(r["beds"], 1) if wants_bed else 0,
        r["floating_fraction"],
        r["pieces"],
    ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="write the full audit here")
    ap.add_argument("--tsv", help="write the audit table here")
    ap.add_argument("--shortlist", action="store_true", help="per-preset ranking")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    rows = audit()
    rows.sort(key=lambda r: (r["flat_pad_verdict"], r.get("floating_fraction", 0.0)))

    if args.shortlist:
        for name in PRESETS:
            best = rank(rows, name)
            p = PRESETS[name]
            print(f"\n=== {name}  pad {p['pad_m']} m  {p['biome']}  "
                  f"({len(best)} of {len(rows)} bodies fit the pad)")
            print(f"    {p['why']}")
            for r in best[:args.top]:
                print(f"  {r['flat_pad_verdict']:18s} {r['name'][:52]:52s} "
                      f"span {r['footprint_span_m']:5.1f} m  "
                      f"air {r['floating_fraction']:5.1%} iso {r['air_isolation_max_m']:5.1f} m "
                      f"worst {r['max_air_m']:5.2f} m  "
                      f"{r['pieces']:6d} pc  st {r['wants_met_n']}/{len(p['wants'])}"
                      f"  {r['top_tier']}({r['top_tier_pieces']})"
                      f"{'  MATERIAL+' + str(r['material_tiers_over']) if r['material_bleed'] else ''}"
                      f"{'  BLEED:' + ','.join(r['tier_bleed']) if r['tier_bleed'] else ''}"
                      f"{'  MISSING:' + str(r['missing_pieces']) if r['missing_pieces'] else ''}")
        return 0

    tally = Counter(r["flat_pad_verdict"] for r in rows)
    geometry_tally = Counter(r["setting"] for r in rows)
    if args.json:
        Path(args.json).write_text(json.dumps({
            "generated_by": "tools/jumpstart/library/audit_bases.py",
            "policy": (
                "Every numeric field is MEASURED by rasterising the body's own collider "
                "solids. `setting` is a function of two of those numbers and nothing "
                "else -- see `setting()`. `intended_setting` is DERIVED FROM CATEGORY, "
                "which geometry cannot supply, and `flat_pad_verdict` is the two "
                "combined. `metadata: derived` rows carry category/kind/role/fit "
                "computed from measurements rather than curated by hand."
            ),
            "isolation_tol_m": ISOLATION_TOL_M,
            "air_area_fraction": bg.BASE_PROFILE_AIR_AREA_FRACTION,
            "flat_tol_m": bg.BASE_PROFILE_FLAT_TOL_M,
            "cell_m": bg.BASE_PROFILE_CELL_M,
            "tally": dict(sorted(tally.items())),
            "geometry_tally": dict(sorted(geometry_tally.items())),
            "presets": {k: rank(rows, k) for k in PRESETS},
            "entries": rows,
        }, ensure_ascii=False, indent=1) + "\n", "utf-8")
    if args.tsv:
        Path(args.tsv).write_text(tsv(rows), "utf-8")
    if not args.json and not args.tsv:
        sys.stdout.write(tsv(rows))
        return 0
    print(f"{len(rows)} bodies audited")
    for k, v in sorted(tally.items()):
        print(f"  {k:20s} {v}")
    print("  -- geometry alone:", dict(sorted(geometry_tally.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
