#!/usr/bin/env python3
"""Web-sourced blueprint candidates: fetch, measure, verdict.

Every source here was found by sweeping GitHub / GitLab / Codeberg code search,
Thunderstore, Nexus Mods, valheimians.com and the Wayback Machine.  Each one is
pinned to a commit, and its licence is recorded as *stated by the source* --
never inferred, never assumed from "it is on a public host".

Two verbs:

    ./web.py --fetch      download every pinned body into staging/ (gitignored)
    ./web.py              measure staging/ and write data/web_roles.{json,tsv}

Viability is resolved against the SAME evidence the sibling inventory uses:
``../blueprints/data/piece_prefabs.json`` -- ASCII tokens harvested from Valheim
1.0.12's own asset bundles, plus tokens unique to each deployed mod plugin.  A
prefab that is in neither is MISSING and the blueprint will place incompletely.

Verdicts, matching the sibling manifest's vocabulary:

    PLACES_CLEAN       every prefab resolves against the game's own bundles
    PLACES_WITH_GAPS   under 2% of pieces unresolved, or mod-supplied
    UNUSABLE           2% or more unresolved
    EMPTY              no pieces
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGING = HERE / "staging"

sys.path.insert(0, str(HERE))
from classify import (  # noqa: E402  -- one parser and one viability rule, shared
    BEDS,
    EVIDENCE,
    PORTALS,
    STATIONS,
    load_evidence,
    parse_blueprint,
    resolve_viability,
    tier_histogram,
    top_tier,
)

# ---------------------------------------------------------------------------
# sources
#
# `redistributable` is the ONLY field that decides whether a body may be
# committed to this repo.  It is a three-value verdict:
#
#   "yes"         the stated licence permits redistribution and is compatible
#                 with publishing this repo -- body IS committed under bodies/
#   "incompatible" a real licence is stated, and it permits redistribution, but
#                 only on terms this repo cannot meet (copyleft).  Local use is
#                 unambiguous; committing is not.
#   "no"          no licence stated, or redistribution expressly forbidden.
# ---------------------------------------------------------------------------

SOURCES: dict[str, dict] = {
    "JereKuusela/valheim-infinity_hammer": {
        "url": "https://github.com/JereKuusela/valheim-infinity_hammer",
        "commit": "ad56b794ef561d95b1786c728b1b5c0df8a2b6f2",
        "licence": "Unlicense (public domain)",
        "licence_quote": (
            "This is free and unencumbered software released into the public domain. "
            "Anyone is free to copy, modify, publish, use, compile, sell, or distribute..."
        ),
        "redistributable": "yes",
        "what": "the placer mod itself; its tests/fixtures/ hold parser-contract blueprints",
        "paths": [
            "tests/fixtures/terrain-blueprint-contract.blueprint",
            "tests/fixtures/terrain-future-section.blueprint",
            "tests/fixtures/terrain-rotated-center.blueprint",
        ],
    },
    "constXife/bygd": {
        "url": "https://github.com/constXife/bygd",
        "commit": "1bb0bf72efe3023620dd355c59d0419add1814f4",
        "licence": "MIT",
        "licence_quote": (
            "Permission is hereby granted, free of charge, to any person obtaining a copy "
            "... to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
            "copies of the Software"
        ),
        "redistributable": "yes",
        "what": "a small server config repo that happens to carry two PiNoKi builds",
        "caveat": (
            "the MIT copyright line reads 'Copyright (c) 2026 constxife' while both bodies "
            "declare '#Creator:PiNoKi'. If constxife is not PiNoKi, the MIT grant does not "
            "reach this content. Recorded, committed, and flagged -- not silently assumed."
        ),
        "paths": [
            "blueprints/PiNoKi_SmallHut.blueprint",
            "blueprints/PiNoKi_Longhouse.blueprint",
        ],
    },
    "RickardAndreasAgren/ValheimBlueprintCruncher": {
        "url": "https://github.com/RickardAndreasAgren/ValheimBlueprintCruncher",
        "commit": "8fbda708d3072b98679682cf001b59f300ae3061",
        "licence": "AGPL-3.0",
        "licence_quote": "GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007",
        "redistributable": "incompatible",
        "what": (
            "a tool for generating damaged/ruined variants of a build, shipped with 21 "
            "worked examples by Raimod: tower, fort and cottage families each in intact, "
            "damaged, ruined and remnant states. The single best non-base find on the web."
        ),
        "caveat": (
            "AGPL-3.0 is strong copyleft. It permits redistribution, but only if the "
            "combined work is itself AGPL, which this repo is not. Fetch and use locally; "
            "do not commit the bodies."
        ),
    },
    "RustyMods/VikingNPC": {
        "url": "https://github.com/RustyMods/VikingNPC",
        "commit": "bf7bbced220982ac6adf02b3e3d149c1d74d4bad",
        "licence": "none stated",
        "licence_quote": "",
        "redistributable": "no",
        "what": (
            "an NPC mod whose spawned settlements are literally blueprints: whole settler "
            "and raider TOWNS per biome, plus a tower and two ruins. 1.0-era (Ashlands "
            "prefabs present)."
        ),
    },
    "Oosquai/SavheimIV": {
        "url": "https://github.com/Oosquai/SavheimIV",
        "commit": "ddb8895574bd88fb74766a50d6f18a418fccc691",
        "licence": "none stated",
        "licence_quote": "",
        "redistributable": "no",
        "what": (
            "a server config repo carrying 44 blueprints: Brokkr's broken-bridge set "
            "(three designs, full and short), Brokkr's cathedral/fort/keep set pieces, and "
            "a graded ruins family."
        ),
    },
    "offsetkeyz/valheim_mod_sync": {
        "url": "https://github.com/offsetkeyz/valheim_mod_sync",
        "commit": "f079c7586529a1aecd095a55697793176faddbe4",
        "licence": "none stated",
        "licence_quote": "",
        "redistributable": "no",
        "what": (
            "a server config repo with portal hubs, portal pyramids and a curved bridge. "
            "MEASURED: its salty-dick-cottage-final.blueprint is BYTE-IDENTICAL "
            "(sha256 6b588d9c...) to this fleet's own corpus copy, which pins the corpus's "
            "upstream and makes this repo a recovery source for truncated files."
        ),
    },
}

# Sources that supply metadata only -- they were surveyed, their terms read, and
# no body was taken.  Kept in the manifest so the next person does not re-walk
# ground that is already known to be closed.
REFERENCE_ONLY_SOURCES: dict[str, dict] = {
    "valheimians.com": {
        "url": "https://www.valheimians.com/builds/?share=downloadable",
        "licence": "redistribution EXPRESSLY FORBIDDEN by site terms",
        "licence_quote": (
            "Terms of Use, General Site Use para 7: users agree not to 'Redistribute any "
            "content, including data, provided by us in any manner whatsoever'. Para 9 "
            "forbids embedding or importing site data into 'data files or application "
            "software'. The upload clause grants a licence to the SITE, not to downstream "
            "consumers."
        ),
        "redistributable": "no",
        "what": (
            "1441 downloadable builds (MEASURED on the live downloadable-filtered index). "
            "By far the largest catalogue found, and almost certainly where this fleet's "
            "own corpus came from -- see PROVENANCE.md."
        ),
    },
    "nexusmods.com/valheim": {
        "url": "https://www.nexusmods.com/valheim",
        "licence": "per-file permissions; the ones readable all forbid reupload",
        "licence_quote": (
            "mods/701 and mods/1916, verbatim: 'Upload permission You are not allowed to "
            "upload this file to other sites under any circumstances'; 'Asset use "
            "permission You must get permission from me before you are allowed to use any "
            "of the assets in this file'."
        ),
        "redistributable": "no",
        "what": (
            "individual .vbuild / .blueprint uploads, mostly 2021-2022 BuildShare era: "
            "tower bridge, seawall harbour, longship dry dock, small Karve dock, basic "
            "watchtower, market stall."
        ),
    },
    "thunderstore.io": {
        "url": "https://thunderstore.io/c/valheim/",
        "licence": "no licensed blueprint pack found",
        "licence_quote": "",
        "redistributable": "no",
        "what": (
            "OverDrive BiomeBlueprints 1.0.3 is the only large pack (353 designs by biome, "
            "no Deep North tab) and states no licence; its contributor form licenses that "
            "mod, not downstream users. PlanBuild and Buildheim are WTFPL but ship only "
            "test fixtures."
        ),
    },
    "sighsorry1029/Homestead": {
        "url": "https://github.com/sighsorry1029/Homestead",
        "licence": "GPL-3.0",
        "licence_quote": "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007",
        "redistributable": "incompatible",
        "what": "4 mod sample blueprints, all bases. Copyleft, same problem as AGPL.",
    },
}

# ---------------------------------------------------------------------------
# curated verdicts, one row per fetched file
#   (category, kind, role, fit)
# category/kind vocabulary is classify.py's.
# ---------------------------------------------------------------------------

CATALOGUE: dict[str, tuple[str, str, str, str]] = {
    # --- Infinity Hammer's own parser-contract fixtures (Unlicense) ----------
    "terrain-blueprint-contract.blueprint": (
        "fixture", "module", "2 wood_floor pieces plus a #TerrainHeight and #TerrainPaint grid",
        "not for the world -- it is the upstream contract for the terrain sections, and the "
        "reference this library's parser is checked against",
    ),
    "terrain-rotated-center.blueprint": (
        "fixture", "module", "2 pieces with #Center:wood_floor and a rotated origin",
        "not for the world -- pins the recentre-around-a-named-piece behaviour",
    ),
    "terrain-future-section.blueprint": (
        "fixture", "module", "deliberately contains unknown sections #FuturePieceData / #FutureTerrainData",
        "not for the world -- its #Description is the authoritative statement that "
        "'Unknown sections must not inherit the preceding parser state', which is what "
        "settles the #Terrain question. Scores UNUSABLE on purpose: `future_payload` is "
        "not a prefab, because it is never meant to be read as one",
    ),
    # --- bygd (MIT) ---------------------------------------------------------
    "PiNoKi_SmallHut.blueprint": (
        "outpost", "module", "one-room gabled hut, 10 x 9 x 3 m, 48 pieces, 8 prefabs",
        "Meadows / Black Forest. The smallest habitable stamp in the whole library and the "
        "only redistributable one -- ideal as a courier shelter or rest stop, twenty times over",
    ),
    "PiNoKi_Longhouse.blueprint": (
        "base", "module", "small longhouse, 16 x 11 x 6 m, 182 pieces",
        "Meadows / Black Forest village filler; pure tier-1 materials",
    ),
    # --- ValheimBlueprintCruncher, Raimod (AGPL-3.0) -------------------------
    # All 21 PLACES_CLEAN, all Black Forest or Meadows tier, all under 600 pieces,
    # all under 19 m footprint.  The ruin/remnant variants are the only graded
    # decay set found anywhere and are exactly the "ruins" flavour category.
    "instairtower.blueprint": ("outpost", "module", "watchtower with internal stair, 10 x 10 x 11 m", "Black Forest watch line"),
    "instairtower_rebuilt.blueprint": ("outpost", "module", "the same tower, patched and reinforced", "Black Forest watch line, 'repaired' variant"),
    "instairtower_damaged.blueprint": ("flavour", "module", "the same tower, battle-damaged", "roadside decay; abandoned-outpost storytelling"),
    "instairtower_ruin.blueprint": ("flavour", "module", "the same tower as a ruin, 96 pieces", "ruins scatter, Black Forest"),
    "instairtower_reruined.blueprint": ("flavour", "module", "the same ruin, further collapsed, 87 pieces", "ruins scatter, Black Forest"),
    "instairtower_remnant.blueprint": ("flavour", "module", "foundation stub only, 30 pieces", "the cheapest ruin stamp in the library"),
    "outposttower.blueprint": ("outpost", "module", "square outpost watchtower, 11 x 11 x 10 m, 212 pieces", "Black Forest / Swamp watch line"),
    "outposttower_burnt.blueprint": ("flavour", "module", "the same tower, burnt out, 114 pieces", "raided-outpost storytelling"),
    "outposttower_ruins.blueprint": ("flavour", "module", "the same tower as a ruin, 115 pieces", "ruins scatter"),
    "outposttower_remnant.blueprint": ("flavour", "module", "footprint stub only, 73 pieces", "ruins scatter"),
    "pillartower.blueprint": ("outpost", "module", "pillar-and-platform lookout, 13 x 8 x 10 m, 199 pieces", "coastal or ridge lookout"),
    "pillartower_ruins.blueprint": ("flavour", "module", "the same lookout as a ruin, 111 pieces", "ruins scatter"),
    "pillartower_remnant.blueprint": ("flavour", "module", "pillar stubs only, 29 pieces", "ruins scatter -- smallest stamp in the library"),
    "foresthold.blueprint": ("defence", "module", "small walled hold, 18 x 12 x 13 m, 585 pieces", "Black Forest road choke point / gatehouse"),
    "foresthold_deserted.blueprint": ("defence", "module", "the same hold, stripped and empty, 515 pieces", "abandoned hold; fine as a claimable objective"),
    "foresthold_ruins.blueprint": ("flavour", "module", "the same hold as a ruin, 285 pieces", "ruins landmark"),
    "foresthold_remnant.blueprint": ("flavour", "module", "wall stub only, 86 pieces", "ruins scatter"),
    "twotwohouse.blueprint": ("base", "module", "two-by-two cottage, 13 x 14 x 7 m, 273 pieces", "Meadows village filler; pure tier-1"),
    "twotwohouse_stormed.blueprint": ("flavour", "module", "the same cottage, roof torn off, 205 pieces", "raided-village storytelling"),
    "twotwohouse_ruins.blueprint": ("flavour", "module", "the same cottage as a ruin, 148 pieces", "ruins scatter"),
    "twotwohouse_remnant.blueprint": ("flavour", "module", "floor plate only, 70 pieces", "ruins scatter"),
    # --- VikingNPC, SiR RuStY SpOoN / GoRe-AdMiN (no licence) ---------------
    "MeadowSettlerTown1.blueprint": ("flavour", "setpiece", "whole settler village, 47 x 53 m, 387 pieces", "Meadows -- a populated-looking hamlet in one stamp"),
    "MeadowSettlerTown2.blueprint": ("flavour", "setpiece", "denser settler village, 31 x 30 m, 997 pieces", "Meadows hamlet"),
    "MeadowRaiderTown1.blueprint": ("defence", "setpiece", "raider camp, 75 x 77 m, 906 pieces", "Meadows hostile camp"),
    "BlackForestRaiderTown1.blueprint": ("defence", "setpiece", "raider camp, 80 x 80 m, 903 pieces", "Black Forest hostile camp"),
    "MountainRaiderTown1.blueprint": ("defence", "setpiece", "raider camp, 63 x 65 m, 1018 pieces", "Mountain hostile camp"),
    "PlainsRaiderTown1.blueprint": ("defence", "setpiece", "raider camp, 79 x 89 m, 1591 pieces", "Plains hostile camp"),
    "MistlandRaiderTown1.blueprint": ("defence", "setpiece", "raider camp, 41 x 51 m, 527 pieces, 65 prefabs", "Mistlands hostile camp"),
    "swamptown1.blueprint": ("defence", "setpiece", "raider camp on stilts, 55 x 53 m, 733 pieces", "Swamp hostile camp"),
    "PlainsRaiderTower1.blueprint": ("outpost", "module", "raider watchtower, 20 x 25 x 23 m, 584 pieces", "Plains watch line"),
    "RuinedTower.blueprint": ("flavour", "module", "ruined tower, 9 x 9 x 12 m, 122 pieces, 9 prefabs", "Black Forest ruins scatter -- excellent repeat stamp"),
    "SettlerRuins_Ashlands1.blueprint": ("flavour", "module", "scattered ruins, 23 x 16 m, 59 pieces", "Ashlands ruins scatter. top_tier reads meadows because its Ashlands content is world STATICS (Ashlands_*, FernAshlands), not craftable ashwood -- so it costs nothing to place and works as a ruin scatter in any biome"),
    # --- SavheimIV, Brokkr / Oosdevforty (no licence) -----------------------
    "brokkr-broken-bridge-1.blueprint": ("bridge", "setpiece", "broken bridge design 1, 87 m span, 2883 pieces, 11 prefabs", "Black Forest gorge; 'ancient ruined crossing' look"),
    "brokkr-broken-bridge-1-short.blueprint": ("bridge", "module", "the same design at 47 m, 1418 pieces", "Black Forest river crossing"),
    "brokkr-broken-bridge-2.blueprint": ("bridge", "setpiece", "broken bridge design 2, 94 m span, 2468 pieces", "Swamp crossing"),
    "brokkr-broken-bridge-2-short.blueprint": ("bridge", "module", "the same design at 57 m, 1407 pieces", "Swamp crossing"),
    "brokkr-broken-bridge-3.blueprint": ("bridge", "setpiece", "broken bridge design 3, 73 m span, 1973 pieces", "Black Forest crossing"),
    "brokkr-broken-bridge-3-short.blueprint": ("bridge", "module", "the same design at 49 m, 1212 pieces", "Black Forest crossing"),
    "ruins_0.blueprint": ("flavour", "module", "tower ruin, 13 x 18 x 35 m, 549 pieces, 7 prefabs", "Black Forest ruins landmark"),
    "ruins_0_1.blueprint": ("flavour", "module", "small ruin, 9 x 9 x 11 m, 85 pieces", "ruins scatter"),
    "ruins_0_2.blueprint": ("flavour", "module", "small ruin, 10 x 10 x 10 m, 132 pieces, 3 prefabs", "ruins scatter -- PLACES_CLEAN, ideal repeat stamp"),
    "ruins_0_3.blueprint": ("flavour", "module", "small ruin, 12 x 10 x 9 m, 140 pieces, 3 prefabs", "ruins scatter -- PLACES_CLEAN, ideal repeat stamp"),
    "ruins_0_1_and_0_2_and_0_3.blueprint": ("flavour", "module", "the three small ruins as one group, 360 pieces", "ruins cluster"),
    "ruins_0_and_0_2.blueprint": ("flavour", "setpiece", "tower ruin plus one small ruin, 681 pieces", "ruins cluster"),
    "ruins_0_and_0_2_and_0_3.blueprint": ("flavour", "setpiece", "tower ruin plus two small ruins, 821 pieces", "ruins cluster"),
    "ruins_0_and_0_1_and_0_2_and_0_3.blueprint": ("flavour", "setpiece", "the whole ruins family as one group, 910 pieces", "ruins cluster"),
    "Meadow_Ruins.blueprint": ("flavour", "setpiece", "meadow ruin field, 34 x 38 x 23 m, 755 pieces, 18 prefabs", "Meadows ruins landmark"),
    "POISWAMPCASTLERUINS.blueprint": ("flavour", "setpiece", "ruined swamp castle, 45 x 45 x 15 m, 1036 pieces", "Swamp ruins landmark -- PLACES_CLEAN"),
    "boom-christianruins1.blueprint": ("flavour", "module", "ruined chapel, 24 x 24 x 8 m, 359 pieces", "ruins landmark"),
    "loki_ruins1.blueprint": ("flavour", "setpiece", "ruin complex, 38 x 35 x 30 m, 1693 pieces", "Mistlands ruins landmark"),
    "loki_ruins2.blueprint": ("flavour", "setpiece", "ruin complex, 33 x 45 x 36 m, 1994 pieces", "Mistlands ruins landmark"),
    "brokkr-the-cathedral.blueprint": ("flavour", "setpiece", "cathedral; two divergent copies exist under this name -- SavheimIV 87 x 92 x 61 m, valheim_mod_sync 85 x 86 x 53 m", "capital landmark. Both place clean; the SavheimIV copy is the larger of the two"),
    "brokkr-fort-torment-final-cut-sh.blueprint": ("defence", "setpiece", "fort, 174 x 175 x 55 m, 8139 pieces", "Ashlands stronghold -- very large"),
    "brokkr-the-belmont.blueprint": ("base", "setpiece", "castle, 65 x 69 x 38 m, 4901 pieces, 8 stations", "Swamp / Mountain showpiece"),
    "brokkr-the-churn.blueprint": ("production", "setpiece", "industrial keep, 71 x 72 x 34 m, 11 stations", "Swamp production centre"),
    "brokkr-the-execution.blueprint": ("flavour", "setpiece", "execution-yard keep, 53 x 50 x 34 m, 5 stations", "grim landmark"),
    "brokkr-the-flabellum.blueprint": ("base", "setpiece", "tall keep, 60 x 38 x 66 m, 13 stations", "Mistlands showpiece"),
    "brokkr-the-misery.blueprint": ("base", "setpiece", "sprawling keep, 95 x 113 x 31 m, 18 stations", "Swamp showpiece"),
    "brokkr-the-sacrifice.blueprint": ("flavour", "setpiece", "temple complex, 78 x 77 x 36 m, 11 stations", "Mistlands landmark"),
    "floatingisland.blueprint": ("flavour", "setpiece", "floating island, 37 x 41 x 97 m", "rejected -- 2.65% of pieces unresolved"),
    "floatingisland_atgeir.blueprint": ("flavour", "setpiece", "floating island, atgeir variant", "rejected -- 2.64% unresolved"),
    "floatingisland_axe.blueprint": ("flavour", "setpiece", "floating island, axe variant", "rejected -- 2.64% unresolved"),
    "floatingisland_battleaxe.blueprint": ("flavour", "setpiece", "floating island, battleaxe variant", "rejected -- 2.64% unresolved"),
    "floatingisland_greatsword.blueprint": ("flavour", "setpiece", "floating island, greatsword variant", "rejected -- 2.64% unresolved"),
    "floatingisland_knife.blueprint": ("flavour", "setpiece", "floating island, knife variant", "rejected -- 2.64% unresolved"),
    "floatingisland_mace.blueprint": ("flavour", "setpiece", "floating island, mace variant", "rejected -- 2.64% unresolved"),
    "floatingisland_sledge.blueprint": ("flavour", "setpiece", "floating island, sledge variant", "rejected -- 2.64% unresolved"),
    "floatingisland_staff.blueprint": ("flavour", "setpiece", "floating island, staff variant", "rejected -- 2.64% unresolved"),
    "floatingisland_sword.blueprint": ("flavour", "setpiece", "floating island, sword variant", "rejected -- 2.64% unresolved"),
    "sh_firtree_light_black.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab, 50% unresolved"),
    "sh_firtree_light_blue.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab"),
    "sh_firtree_light_green.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab"),
    "sh_firtree_light_orange.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab"),
    "sh_firtree_light_purple.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab"),
    "sh_firtree_light_red.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab"),
    "sh_firtree_light_yellow.blueprint": ("flavour", "module", "2-piece coloured fir-tree light", "rejected -- mod-only prefab"),
    # --- valheim_mod_sync, Salty Dick / Nurr / Hong / Pipkin (no licence) ---
    "salty-dick-portal-hub-final.blueprint": ("portal", "setpiece", "portal hub, 28 x 28 x 15 m, 8 portals, 1043 pieces", "central portal hub. teleportall is ON here, so a hub is worth far more than in vanilla"),
    "nurr-portalpyramid02.blueprint": ("portal", "setpiece", "portal pyramid, 33 x 33 x 21 m, 8 portals, 796 pieces", "secondary portal hub"),
    "Hong_Marble_Telepyramid.blueprint": ("portal", "module", "marble telepyramid, 20 x 20 x 15 m, 1 portal, 396 pieces", "THE roadside portal shrine pattern -- one portal, small footprint, stamp everywhere"),
    "Hong_Marble_Telepyramid_(Shielded).blueprint": ("portal", "module", "the same pyramid with a ward, 398 pieces", "roadside portal shrine, protected variant"),
    "salty-dick-bridge-curved-final.blueprint": ("bridge", "module", "curved bridge, 34 x 64 x 25 m, 1242 pieces, only 5 prefabs", "Mistlands-tier crossing; the curve suits a road bending round terrain"),
    "salty-dick-cottage-final.blueprint": ("base", "module", "cottage, 26 x 23 x 16 m, 877 pieces", "BYTE-IDENTICAL to the fleet corpus copy -- this is the recovery source"),
    "\u041f\u0438\u043f\u043a\u0438\u043d_full_castle.blueprint": ("base", "setpiece", "castle, 192 x 532 x 213 m, 32361 pieces", "reference only -- a 532 m footprint is not siteable"),
    "\u041f\u0438\u043f\u043a\u0438\u043d_small_castle.blueprint": ("base", "setpiece", "castle, 87 x 75 x 97 m, 17628 pieces", "reference only"),
}



def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def fetch() -> int:
    STAGING.mkdir(exist_ok=True)
    written = 0
    for repo, spec in SOURCES.items():
        out_dir = STAGING / repo.replace("/", "__")
        out_dir.mkdir(exist_ok=True)
        paths = spec.get("paths")
        if paths is None:
            api = f"https://api.github.com/repos/{repo}/git/trees/{spec['commit']}?recursive=1"
            tree = json.loads(_get(api, json_api=True))["tree"]
            paths = [e["path"] for e in tree if e["path"].lower().endswith((".blueprint", ".vbuild"))]
        for path in paths:
            name = path.rsplit("/", 1)[-1]
            target = out_dir / name
            if target.exists() and target.stat().st_size:
                continue
            raw = (
                f"https://raw.githubusercontent.com/{repo}/{spec['commit']}/"
                + urllib.parse.quote(path)
            )
            target.write_bytes(_get(raw))
            written += 1
        print(f"{repo}: {len(list(out_dir.iterdir()))} files staged ({spec['licence']})")
    print(f"{written} newly downloaded into {STAGING}")
    return 0


def _get(url: str, json_api: bool = False) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "valheim-portal/jumpstart-library"})
    if json_api:
        req.add_header("Accept", "application/vnd.github+json")
    return urllib.request.urlopen(req, timeout=60).read()


def measure() -> list[dict]:
    if not STAGING.is_dir():
        raise SystemExit(f"nothing staged. run ./web.py --fetch first ({STAGING} missing)")
    vanilla, mod_owner = load_evidence()
    rows: list[dict] = []
    for repo, spec in SOURCES.items():
        out_dir = STAGING / repo.replace("/", "__")
        if not out_dir.is_dir():
            continue
        for path in sorted(out_dir.iterdir()):
            if path.suffix.lower() not in (".blueprint", ".vbuild"):
                continue
            parsed = parse_blueprint(path)
            pieces = parsed["pieces"]
            counts = Counter(prefab for prefab, _x, _y, _z in pieces)
            viability = resolve_viability(counts, vanilla, mod_owner)
            if pieces:
                xs = [p[1] for p in pieces]
                ys = [p[2] for p in pieces]
                zs = [p[3] for p in pieces]
                footprint = [
                    round(max(xs) - min(xs), 1),
                    round(max(zs) - min(zs), 1),
                    round(max(ys) - min(ys), 1),
                ]
            else:
                footprint = [0.0, 0.0, 0.0]
            hist = tier_histogram(counts)
            tier, tier_pieces = top_tier(hist)
            curated = CATALOGUE.get(path.name)
            if curated is None:
                raise SystemExit(f"CATALOGUE is missing a verdict for {repo}/{path.name}")
            category, kind, role, fit = curated
            rows.append(
                {
                    "file": path.name,
                    "origin": repo,
                    "source_url": spec["url"],
                    "commit": spec["commit"],
                    "licence": spec["licence"],
                    "redistributable": spec["redistributable"],
                    "committed_body": spec["redistributable"] == "yes",
                    "format": parsed["fmt"],
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "blueprint_name": parsed["meta"].get("name", ""),
                    "creator": parsed["meta"].get("creator", ""),
                    "category": category,
                    "kind": kind,
                    "role": role,
                    "fit": fit,
                    **viability,
                    "pieces": len(pieces),
                    "distinct_prefabs": len(counts),
                    "footprint_xzy": footprint,
                    "top_tier": tier,
                    "top_tier_pieces": tier_pieces,
                    "stations": sum(counts[s] for s in STATIONS if s in counts),
                    "portals": sum(counts[p] for p in PORTALS if p in counts),
                    "beds": sum(counts[b] for b in BEDS if b in counts),
                    "snap_points": parsed["snap_points"],
                    "terrain_height_rows": parsed["terrain_height"],
                    "terrain_paint_rows": parsed["terrain_paint"],
                    "ih_discarded_rows": sum(parsed["discarded"].values()),
                    "unknown_sections": parsed["unknown_sections"],
                    "pieces_after_unknown": parsed["pieces_after_unknown"],
                    "legacy_terrain_rejected": parsed["legacy_terrain"],
                }
            )
    return rows


TSV_COLUMNS = (
    "file", "origin", "licence", "redistributable", "category", "kind", "verdict",
    "pieces", "distinct_prefabs", "footprint", "top_tier", "missing_pct", "portals",
    "stations", "ih_discarded_rows", "creator", "role", "fit",
)


def tsv(rows: list[dict]) -> str:
    out = ["\t".join(TSV_COLUMNS)]
    for r in rows:
        fx, fz, fy = r["footprint_xzy"]
        cells = []
        for col in TSV_COLUMNS:
            value = f"{fx}x{fz}x{fy}" if col == "footprint" else r[col]
            cells.append(str(value).replace("\t", " ").replace("\n", " "))
        out.append("\t".join(cells))
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true", help="download every pinned body into staging/")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--json", default=str(HERE / "data" / "web_roles.json"))
    ap.add_argument("--tsv", default=str(HERE / "data" / "web_roles.tsv"))
    args = ap.parse_args()

    if args.fetch:
        return fetch()

    rows = measure()
    if args.show:
        sys.stdout.write(tsv(rows))
        return 0
    payload = {
        "generated_by": "tools/jumpstart/library/web.py",
        "viability_basis": str(EVIDENCE.relative_to(HERE.parent.parent.parent)),
        "sources": SOURCES,
        "reference_only_sources": REFERENCE_ONLY_SOURCES,
        "verdict_tally": dict(sorted(Counter(r["verdict"] for r in rows).items())),
        "category_tally": dict(sorted(Counter(r["category"] for r in rows).items())),
        "redistributable_tally": dict(sorted(Counter(r["redistributable"] for r in rows).items())),
        "entries": rows,
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")
    Path(args.tsv).write_text(tsv(rows), "utf-8")
    print(f"{len(rows)} web candidates -> {args.json}, {args.tsv}")
    print("verdicts:      ", payload["verdict_tally"])
    print("categories:    ", payload["category_tally"])
    print("redistributable:", payload["redistributable_tally"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
