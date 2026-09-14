#!/usr/bin/env python3
"""Re-mine the on-disk PlanBuild corpus under a *structure-role* lens.

``../blueprints/inventory.py`` answers "will this file place on 1.0.12?".  This
script answers a different question: **what is this thing, and can I stamp it
twenty times?**  It exists because the first pass through the corpus was looking
for one base per progression tier and therefore never catalogued the bridges,
gateway arches, watchtowers, market rows, refineries and camps that were sitting
in the same directory.

Everything numeric here is MEASURED by parsing the file:

  pieces, distinct prefabs, axis-aligned footprint, per-tier material histogram,
  station / portal / bed / ward counts, ZDO-spam offenders, and the number of
  terrain-edit rows that Infinity Hammer silently discards.

Everything in ``CATALOGUE`` is CURATED -- a human verdict on category, role,
repeatability and where in the world the thing belongs.  The two are kept
separate on purpose so a future reader can tell which is which.

Usage:
    ./classify.py                          # writes data/library_manifest.{json,tsv}
    ./classify.py --print                  # table to stdout, writes nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent

# MEASURED: the two directories holding this fleet's corpus.  Same paths the
# sibling manifest uses; four `old`s deep, so the checksums are what will tell
# us when a cleanup deletes them.
CORPUS_DIRS = (
    Path(
        "/media/big4/projects/game/valheim/old/old/old/old_Storgard"
        "/config_merged/BepInEx/PlanBuild/blueprints"
    ),
    Path(
        "/media/big4/projects/game/valheim/old/bp_old/config/default"
        "/bepinex_old/PlanBuild/blueprints"
    ),
)

# ---------------------------------------------------------------------------
# parser -- deliberately self-contained
#
# It duplicates ../blueprints/inventory.py rather than importing it, because
# this script must keep working while that module is being edited, and because
# it models one thing that module gets wrong: what Infinity Hammer does with an
# UNKNOWN `#Section` header.
#
# MEASURED, by disassembling the DEPLOYED
#   Ulfsland/data/bepinex/BepInEx/plugins/Infinity_Hammer/InfinityHammer.dll
# with `monodis` against the game's own managed assemblies.
# `HammerBlueprintCommand::GetPlanBuild` is a five-state machine
#   enum PlanBuildSection { None=0, Pieces=1, SnapPoints=2, TerrainHeight=3, TerrainPaint=4 }
# initialised to Pieces -- a headerless .blueprint is all pieces -- and driven
# per row as:
#
#   isHeader = row.StartsWith("#", OrdinalIgnoreCase)
#   if isHeader:
#       gridRow = 0
#       if section in {TerrainHeight, TerrainPaint}: section = None
#   #name: #creator: #description: #category: #center: #coordinates: #rotation:
#                        -> assign field, next row
#   #height: / #paint:   -> THROW "Legacy #Height/#Paint terrain format is no
#                           longer supported. Re-save the blueprint with a newer
#                           Infinity Hammer version."
#   #terrainheight:      -> section = TerrainHeight
#   #terrainpaint:       -> section = TerrainPaint
#   #snappoints          -> section = SnapPoints
#   #pieces              -> section = Pieces
#   any OTHER header:
#       row.Length == 1 or char.IsWhiteSpace(row[1])  ->  a COMMENT; section kept
#       otherwise                                     ->  section = None
#   switch (section - 1): Pieces / SnapPoints / TerrainHeight / TerrainPaint row;
#                         None falls through the switch default and the row is
#                         DISCARDED.
#
# So `#Terrain` -- PlanBuild's own terrain-edit section, rows
# `shape;x;y;z;radius;rotation;smooth;` -- is an unknown header whose second
# character is not whitespace.  Infinity Hammer therefore sets section = None and
# **silently discards every row until the next recognised header**.  Two things
# follow, and both are recorded per file below:
#
#   ih_discarded_rows  terrain edits Infinity Hammer throws away.  The captured
#                      flattening is LOST; level the ground by hand first.
#   pieces_at_risk     pieces that sit after an unknown header with no
#                      recognised header in between, and would be dropped
#                      entirely.  MEASURED as 0 across the whole corpus: every
#                      `#Terrain` block is written immediately before `#Pieces`.
#
# The upstream author's own parser-contract fixture agrees, in as many words:
# `tests/fixtures/terrain-future-section.blueprint`, `#Description:Unknown
# sections must not inherit the preceding parser state`.
#
# A reader that instead keeps the current section -- which is what
# ../blueprints/inventory.py does -- reads those rows as PIECES named `circle`
# and `square`.  That is not merely cosmetic: `circle` and `square` both happen
# to occur as ASCII tokens inside the game's asset bundles, so the bundle-token
# viability check scores them as vanilla and the files still read PLACES_CLEAN,
# while `to_rcon_plan.py` would emit `spawn circle ...` for each one.
#
# Piece and .vbuild field layout MEASURED from the same disassembly
# (GetPlanBuildObject / GetBuildShareObject).
# ---------------------------------------------------------------------------

HEADER_KEYS = ("name", "creator", "description", "category", "center", "coordinates", "rotation")
# Recognised section headers, and the state each selects.
SECTION_HEADERS = (
    ("#pieces", "pieces"),
    ("#snappoints", "snappoints"),
    ("#terrainheight:", "terrainheight"),
    ("#terrainpaint:", "terrainpaint"),
)


def _f(parts: list[str], index: int, default: float) -> float:
    if index >= len(parts):
        return default
    try:
        return float(parts[index])
    except ValueError:
        return default


def parse_blueprint(path: Path) -> dict:
    text = path.read_text("utf-8", "replace")
    meta: dict[str, str] = {}
    pieces: list[tuple[str, float, float, float]] = []
    snap = terrain_height = terrain_paint = 0
    discarded: Counter[str] = Counter()
    unknown_sections: list[str] = []
    pieces_after_unknown = 0
    legacy_terrain = False

    if path.suffix.lower() == ".vbuild":
        # split(" ") with empties KEPT -- whitespace-run splitting shifts every
        # column, because a zero component serialises as an empty field.
        for row in text.splitlines():
            line = row.rstrip("\r")
            if not line.strip():
                continue
            parts = line.replace(",", ".").split(" ") if "," in line else line.split(" ")
            prefab = parts[0].strip()
            if prefab:
                pieces.append((prefab, _f(parts, 5, 0.0), _f(parts, 6, 0.0), _f(parts, 7, 0.0)))
        return dict(
            meta=meta, pieces=pieces, snap_points=0, terrain_height=0, terrain_paint=0,
            discarded=discarded, unknown_sections=[], pieces_after_unknown=0,
            legacy_terrain=False, fmt="vbuild",
        )

    section = "pieces"  # PlanBuildSection.Pieces, as Infinity Hammer initialises it
    for row in text.splitlines():
        if not row:
            continue
        low = row.lower()
        if low.startswith("#"):
            if section in ("terrainheight", "terrainpaint"):
                section = "none"
            for key in HEADER_KEYS:
                if low.startswith("#" + key + ":"):
                    meta[key] = row[len(key) + 2:].strip()
                    break
            else:
                if low.startswith("#height:") or low.startswith("#paint:"):
                    legacy_terrain = True  # Infinity Hammer THROWS on this file
                    section = "none"
                    continue
                for prefix, state in SECTION_HEADERS:
                    if low.startswith(prefix):
                        section = state
                        break
                else:
                    # Unknown header. Length 1 or whitespace at [1] -> comment.
                    if len(row) > 1 and not row[1].isspace():
                        unknown_sections.append(row.split(":")[0])
                        section = "none"
            continue
        if section == "snappoints":
            snap += 1
            continue
        if section == "terrainheight":
            terrain_height += 1
            continue
        if section == "terrainpaint":
            terrain_paint += 1
            continue
        if section == "none":
            if row.strip():
                discarded[row.split(";")[0].strip()] += 1
            continue
        parts = row.replace(",", ".").split(";")
        prefab = parts[0].strip()
        if prefab:
            pieces.append((prefab, _f(parts, 2, 0.0), _f(parts, 3, 0.0), _f(parts, 4, 0.0)))
            if unknown_sections:
                # A recognised header re-armed the parser after an unknown
                # section, so nothing structural was lost to it.  If this stays
                # 0 on a file that HAS unknown sections, pieces were swallowed.
                pieces_after_unknown += 1
    return dict(
        meta=meta, pieces=pieces, snap_points=snap, terrain_height=terrain_height,
        terrain_paint=terrain_paint, discarded=discarded,
        unknown_sections=unknown_sections, pieces_after_unknown=pieces_after_unknown,
        legacy_terrain=legacy_terrain, fmt="blueprint",
    )


# ---------------------------------------------------------------------------
# measured signals
# ---------------------------------------------------------------------------

# Prefab-name prefixes that pin a material to a progression tier.  Order
# matters: first match wins, so the specific `piece_groundtorch_mist` must come
# before any generic prefix that could also match it.
TIER_PREFIX: tuple[tuple[str, str], ...] = (
    ("charred_", "ashlands"),
    ("ashwood_", "ashlands"),
    ("Piece_grausten", "ashlands"),
    ("piece_grausten", "ashlands"),
    ("piece_groundtorch_mist", "mistlands"),
    ("blackmarble", "mistlands"),
    ("piece_dvergr", "mistlands"),
    ("dvergrprops", "mistlands"),
    ("crystal_wall", "mistlands"),
    ("eitrrefinery", "mistlands"),
    ("yggdrasil_", "mistlands"),
    ("CastleKit_", "mistlands"),
    ("caverock_ice", "mountain"),
    ("blastfurnace", "mountain"),
    ("windmill", "plains"),
    ("piece_spinningwheel", "plains"),
    ("goblin_", "plains"),
    ("iron_wall", "swamp"),
    ("iron_floor", "swamp"),
    ("iron_beam", "swamp"),
    ("iron_grate", "swamp"),
    ("woodiron_", "swamp"),
    ("darkwood_", "blackforest"),
    ("stone_wall", "blackforest"),
    ("stone_arch", "blackforest"),
    ("stone_pillar", "blackforest"),
    ("stone_stair", "blackforest"),
    ("piece_stonecutter", "blackforest"),
)
TIER_ORDER = ("meadows", "blackforest", "swamp", "mountain", "plains", "mistlands", "ashlands")

# Crafting stations and station extensions. Every name here was confirmed to
# occur in the corpus or a staged web candidate -- guessed names such as
# `piece_smelter` or a bare `bathtub` are deliberately absent, because a station
# count that silently reads zero is worse than no count.
STATIONS = frozenset(
    """piece_workbench piece_workbench_ext1 piece_workbench_ext2
    piece_workbench_ext3 piece_workbench_ext4
    forge forge_ext1 forge_ext2 forge_ext3 forge_ext4 forge_ext5 forge_ext6
    blackforge blackforge_ext1 blackforge_ext2_vise blackforge_ext3_metalcutter
    blackforge_ext4_gemcutter
    piece_cauldron cauldron_ext1_spice cauldron_ext3_butchertable
    cauldron_ext4_pots cauldron_ext5_mortarandpestle cauldron_ext6_rollingpins
    piece_MeadCauldron piece_cookingstation piece_cookingstation_iron
    piece_oven piece_preptable
    piece_magetable piece_magetable_ext piece_magetable_ext2 piece_magetable_ext3
    piece_artisanstation artisan_ext1 piece_stonecutter piece_spinningwheel
    smelter charcoal_kiln blastfurnace windmill eitrrefinery
    piece_beehive piece_sapcollector piece_barber piece_bathtub""".split()
)
PORTALS = frozenset(("portal_wood", "portal", "piece_portal"))
BEDS = frozenset(("bed", "piece_bed02"))
WARDS = frozenset(("guard_stone",))
# Prefabs whose sheer multiplicity is the problem, not the prefab. Each is a
# persistent ZDO carrying a light, a text field, a physics hull or -- in the
# case of a station extension used as wall cladding -- a crafting-level bonus.
SPAM_HINTS = ("sign", "itemstand", "torch", "brazier", "forge_ext", "ledge")
SPAM_FLOOR = 120


def tier_histogram(counts: Counter[str]) -> Counter[str]:
    hist: Counter[str] = Counter()
    for prefab, n in counts.items():
        for prefix, tier in TIER_PREFIX:
            if prefab.startswith(prefix):
                hist[tier] += n
                break
        else:
            hist["meadows"] += n
    return hist


def top_tier(hist: Counter[str]) -> tuple[str, int]:
    """Highest tier present, and how many pieces sit at it.

    The count is the point.  A 238-piece cottage with 40 ashwood trim pieces is
    not an Ashlands build -- it is a Meadows build with substitutable trim -- and
    only the count tells you which case you are in.
    """
    present = [t for t in TIER_ORDER if hist.get(t)]
    tier = present[-1] if present else "meadows"
    return tier, hist.get(tier, 0)


# ---------------------------------------------------------------------------
# viability
#
# Resolved against the same evidence the sibling inventory uses:
# ../blueprints/data/piece_prefabs.json -- ASCII tokens harvested from Valheim
# 1.0.12's own asset bundles (StreamingAssets/SoftRef/Bundles), plus the tokens
# unique to each deployed BepInEx plugin directory.  That distinguishes "Iron
# Gate removed this piece" from "a mod we happen to have supplies it".
#
# The token set is a superset of the real prefab table -- it is every ASCII run
# in the bundles -- so it can only ever produce FALSE NEGATIVES, never false
# MISSING. `circle` and `square` are exactly that case: not building pieces, but
# present as tokens, so a reader that mistakes PlanBuild's `#Terrain` rows for
# pieces still scores PLACES_CLEAN. This library does not make that mistake --
# see the parser note above.
# ---------------------------------------------------------------------------

EVIDENCE = HERE.parent / "blueprints" / "data" / "piece_prefabs.json"


def load_evidence() -> tuple[set[str], dict[str, str]]:
    data = json.loads(EVIDENCE.read_text("utf-8"))
    owner: dict[str, str] = {}
    for mod, tokens in data["mods"].items():
        for token in tokens:
            owner.setdefault(token, mod)
    return set(data["vanilla"]), owner


def resolve_viability(
    counts: Counter[str], vanilla: set[str], mod_owner: dict[str, str]
) -> dict:
    """Verdict vocabulary matches ../blueprints/data/corpus_manifest.json."""
    missing: dict[str, int] = {}
    mods: dict[str, list] = {}
    for prefab, n in counts.items():
        if prefab in vanilla:
            continue
        if prefab in mod_owner:
            mods[prefab] = [n, mod_owner[prefab]]
        else:
            missing[prefab] = n
    total = sum(counts.values())
    pct = round(100 * sum(missing.values()) / total, 2) if total else 0.0
    if not total:
        verdict = "EMPTY"
    elif pct >= 2:
        verdict = "UNUSABLE"
    elif missing or mods:
        verdict = "PLACES_WITH_GAPS"
    else:
        verdict = "PLACES_CLEAN"
    return {
        "verdict": verdict,
        "missing_pct": pct,
        "missing_prefabs": dict(sorted(missing.items(), key=lambda kv: -kv[1])),
        "mod_prefabs": mods,
    }


# ---------------------------------------------------------------------------
# curated catalogue -- the human half
#
#   category:   base bridge portal outpost production defence flavour dock road
#   kind:       module   -> small and stampable many times over
#               setpiece -> a one-off showpiece
#               exploit  -> a loot/progression payload, not a structure
#               empty    -> 0-byte truncated download
#   fit:        where in Ulfsland this earns its place (biome / tier / purpose)
# ---------------------------------------------------------------------------

CATALOGUE: dict[str, tuple[str, str, str, str, str]] = {
    # file                                            category      kind        role                                  fit                                                              note
    "14464-Valheimian.blueprint": ("base", "exploit", "dvergr house shell wrapped round 491 loot objects", "nowhere -- 488 mountain-cave chests", "byte-identical to gnome_house; 1 discarded #Terrain row"),
    "15643-Valheimian.blueprint": ("base", "exploit", "keep wrapped round 361 loot objects", "nowhere -- chest payload", "also names 4 mod-only pieces incl. turrets; 2 discarded rows"),
    "BjOrN_blueprint001.blueprint": ("base", "setpiece", "palisaded longhouse compound", "Black Forest / pre-bonemass -- already selected", "978 log-wall pieces: the palisade is most of it"),
    "Black_Viking_forteca_starting_base.blueprint": ("base", "setpiece", "walled starter compound", "Meadows -- reference only", "0.8% missing, incl. mod-only emberwood + Hayze gates"),
    "Portal11.vbuild": ("portal", "module", "timber gateway arch, 3 x 3 m, no portal piece included", "any road junction; drop a portal_wood inside", "darkwood beams + stone floor; stamp repeatedly"),
    "Portal12.vbuild": ("portal", "module", "stone gateway arch, 2 x 5 m, itemstand plinth", "roadside shrine; Black Forest stone tier", "44 stone_arch; smallest of the five at 66 pieces"),
    "Portal13.vbuild": ("portal", "module", "timber-and-stone gate house, 4 x 7 m", "portal shack pattern -- already selected pre-kall", "133 pieces; roofed, so the portal stays dry"),
    "Portal14.vbuild": ("portal", "module", "stone arch gateway with stair, 4 x 5 m", "hillside road, Black Forest+", "161 pieces; includes a Rock_4 for the base"),
    "Portal15.vbuild": ("portal", "module", "double stone arch shrine, 4 x 7 m", "runestone-style roadside shrine", "107 pieces; heaviest stone ratio of the five"),
    "PuP_Bone-dragon.blueprint": ("flavour", "setpiece", "skeletal dragon landmark built from rock and rib fractures", "Mistlands or Ashlands skyline landmark", "108 x 109 x 114 m; 926 statics, no building pieces at all"),
    "PuP_Capitol.blueprint": ("base", "setpiece", "large timber capitol hall", "Plains / pre-yagluth settlement centre", "2304 pieces"),
    "PuP_Minicastle.blueprint": ("base", "setpiece", "compact grausten castle", "Ashlands / pre-fader", "1408 blank sign_notext used as decor panels -- ZDO hazard"),
    "PuP_Owl-tree_house.blueprint": ("base", "empty", "0-byte truncated download", "-", "re-download needed"),
    "PuP_black_house_full.blueprint": ("base", "setpiece", "large black-marble manor", "Mistlands / pre-queen -- already selected", "6262 pieces, the largest clean base in the corpus"),
    "PuP_black_house_light.blueprint": ("base", "setpiece", "black-marble manor, reduced", "Mistlands / pre-queen alternative", "5398 pieces"),
    "PuP_capitoliy.blueprint": ("base", "setpiece", "grausten-and-crystal capitol", "Ashlands / Deep North showpiece", "2149 wood_ledge pieces -- heavy ledge lattice"),
    "PuP_forge1.blueprint": ("production", "module", "roofed forge and workshop shed, 19 x 13 m, 10 stations", "workshop annex bolted onto any existing base", "343 pieces; the cheapest station upgrade in the corpus"),
    "PuP_house1.blueprint": ("base", "module", "two-storey cottage, 10 x 8 m", "Meadows / Black Forest village filler", "238 pieces; 40 ashwood trim pieces are substitutable"),
    "PuP_house10.blueprint": ("base", "module", "wide cottage with porch, 19 x 16 m", "Meadows village filler", "225 pieces"),
    "PuP_house11.blueprint": ("base", "module", "log cottage, 13 x 10 m", "Black Forest village filler", "266 pieces; 2 stations"),
    "PuP_house12.blueprint": ("base", "module", "small cottage, 11 x 9 m", "Meadows village filler", "216 pieces, only 5.7 m tall -- fits under trees"),
    "PuP_house13.blueprint": ("base", "module", "log cabin, 10 x 14 m", "Black Forest village filler", "379 pieces; 17 blank signs used as shutters"),
    "PuP_house2.blueprint": ("base", "module", "gabled cottage, 10 x 13 m", "Meadows village filler", "302 pieces"),
    "PuP_house3.blueprint": ("base", "module", "long low cottage, 13 x 16 m", "Meadows village filler", "201 pieces"),
    "PuP_house4.blueprint": ("base", "module", "cottage, 10 x 10 m", "Meadows / Black Forest village filler", "178 pieces; 1 station. An earlier pass called this a workshop annex on a station count that was wrongly counting dvergr poles"),
    "PuP_house5.blueprint": ("base", "module", "cottage with outbuilding, 10 x 12 m", "Meadows village filler", "291 pieces; 3 stations"),
    "PuP_house6.blueprint": ("base", "module", "cottage with balcony, 12 x 9 m", "Meadows village filler", "230 pieces"),
    "PuP_house7.blueprint": ("base", "module", "tiny cottage, 8 x 10 m", "hunting camp / rest stop shell", "152 pieces -- second-smallest habitable stamp"),
    "PuP_house8.blueprint": ("base", "module", "log hut, 10 x 9 m", "Black Forest hunting camp", "157 pieces, 5.3 m tall"),
    "PuP_house9.blueprint": ("base", "module", "hut, 10 x 10 m", "mine-head shelter", "167 pieces; 0 stations -- shelter only"),
    "PuP_mag-tower.blueprint": ("flavour", "setpiece", "mage tower, 24 x 21 x 36 m", "Mistlands landmark", "2461 pieces"),
    "PuP_marketmarket.blueprint": ("flavour", "module", "market stall row, 15 x 15 m, 39 banners", "trader settlement / festival ground", "257 pieces; 1 missing prefab `market` (0.39%)"),
    "PuP_megaCastle.blueprint": ("base", "setpiece", "castle city, 209 x 105 x 133 m", "nowhere sensible -- reference only", "25207 pieces; 28 core stands hand out free progression"),
    "PuP_rampart1.blueprint": ("defence", "empty", "0-byte -- would have been the corpus's only pure rampart", "-", "re-download is the single highest-value recovery"),
    "PuP_stable.blueprint": ("production", "empty", "0-byte -- would have been the corpus's only stable", "-", "re-download wanted; nothing else covers pens"),
    "PuP_tavern.blueprint": ("flavour", "empty", "0-byte truncated download", "-", "re-download needed"),
    "SerpentShip.blueprint": ("flavour", "empty", "0-byte -- serpent ship sculpture", "-", "re-download needed"),
    "SerpentShip_Empty.blueprint": ("flavour", "empty", "0-byte -- serpent ship sculpture, hollow", "-", "re-download needed"),
    "Ultimate_OutPost.blueprint": ("outpost", "setpiece", "walled outpost, 53 x 49 m, 54 stations, 450 wall pieces", "Deep North sandbox -- already selected", "1820 pieces; the corpus's best all-in-one forward base"),
    "altar.blueprint": ("flavour", "setpiece", "open-air altar / assembly bowl, 21 x 20 m, 88 seats", "festival ground, any biome", "527 pieces; 8 snap points; stampable despite the size"),
    "aodi-skardvakt-the-black-tower.blueprint": ("outpost", "module", "black-marble watchtower, 9 x 9 x 11 m, bonfire on top", "Mistlands watch line; signal-fire chain", "358 pieces from only 7 prefabs -- cheap and legible. 8 #Terrain rows discarded, so flatten the ground first"),
    "arty-dragontavern.blueprint": ("flavour", "setpiece", "dragon-themed tavern", "settlement centre", "6852 pieces; 188 signs + 200 itemstands; needs mod rk_brazier"),
    "arty-magetower.blueprint": ("flavour", "setpiece", "mage tower, 37 x 33 x 44 m", "Mistlands landmark", "4948 pieces; 558 itemstands; 0.36% missing incl. mod pieces"),
    "berserkers-keep.vbuild": ("base", "setpiece", "keep with 52 stations and 2515 wall pieces", "Plains stronghold", "10686 pieces; legacy .vbuild, so no scale and no data payloads"),
    "bridge.vbuild": ("bridge", "setpiece", "timber trestle bridge, 39 x 58 m span", "Black Forest river crossing", "873 pieces; 105 itemstands as railing decor"),
    "citadel.blueprint": ("base", "setpiece", "citadel, 147 x 117 x 127 m", "reference only -- too big to site", "33655 pieces; 361 unlit castle torches"),
    "cozy_house.blueprint": ("base", "setpiece", "large cosy homestead, 37 stations", "Plains / pre-yagluth -- already selected", "6034 pieces"),
    "creator-looney-swamp-starter-home.blueprint": ("base", "module", "core-wood stilt house, 17 x 15 m", "Swamp / pre-bonemass forward camp", "218 pieces, PURE Meadows-tier materials, 7 snap points"),
    "deardly-casa-drly.blueprint": ("base", "setpiece", "ashwood villa", "Ashlands / pre-fader -- already selected", "1901 pieces"),
    "dock.blueprint": ("dock", "setpiece", "harbour with warehouse, 42 x 29 m", "any coast -- already selected twice", "2036 pieces; 8 snap points"),
    "drake-lighthouse.blueprint": ("flavour", "module", "lighthouse, 14 x 14 x 48 m", "headland navigation landmark, any coast", "1255 pieces but only a 14 m footprint -- stampable on any cape"),
    "forge.blueprint": ("production", "setpiece", "walled forge yard, 31 x 26 m, 18 stations", "ore-country smelting yard", "2968 pieces; 8 snap points"),
    "forge (copy 1).blueprint": ("production", "setpiece", "byte-identical duplicate of forge.blueprint", "-", "deduplicate; do not select by this name"),
    "fortress.blueprint": ("defence", "setpiece", "fortress, 76 x 51 x 34 m, 4 beds", "Plains / Mistlands strongpoint", "10626 pieces; 6 snap points"),
    "fox-0123456.blueprint": ("base", "setpiece", "meadow homestead with 4 portals", "Meadows / pre-elder -- already selected", "1229 pieces, ALL of them Meadows-tier -- the only fully tier-1 build of over a thousand pieces in the corpus"),
    "gnome_house.blueprint": ("base", "exploit", "byte-identical to 14464-Valheimian", "nowhere", "loot payload under a misleading name"),
    "god-2.blueprint": ("outpost", "module", "stone watchtower, 9 x 10 x 22 m", "Black Forest / Swamp watch line", "702 pieces; 8 snap points; stone + woodiron only"),
    "god-22.blueprint": ("outpost", "module", "stone watchtower, wider variant, 12 x 12 x 23 m", "Black Forest / Swamp watch line", "836 pieces; sibling of god-2"),
    "god-3.blueprint": ("outpost", "module", "stone tower barracks, 16 x 17 x 19 m, 4 beds", "forward rest stop with beds", "1518 pieces; 28 snap points -- the most snap-friendly file in the corpus"),
    "god-house33.blueprint": ("base", "setpiece", "manor, 30 x 47 m", "Mistlands settlement", "2949 pieces; 1 discarded #Terrain row"),
    "god-o.blueprint": ("defence", "module", "stone gate arch, 7 x 5 x 10 m", "palisade gateway; road choke point", "194 pieces from 7 prefabs -- the cheapest gatehouse available"),
    "god-ponte-plus.blueprint": ("bridge", "module", "stone-and-iron bridge, 27 m span, 8 x 27 x 19 m", "Swamp / Mountain ravine crossing", "883 pieces from 8 prefabs; 2 snap points; 2 #Terrain rows discarded"),
    "god-portaaoo.blueprint": ("defence", "setpiece", "monumental stone gatehouse, 36 x 12 x 23 m", "settlement main gate -- already selected in deepnorth-sandbox as sandbox-portal-hub", "2191 pieces, zero beds, zero stations. Filed as a base elsewhere; 'porta' is the clue -- it is a GATE, not a dwelling, and wants something habitable beside it"),
    "god-shadowstone-fortress.blueprint": ("defence", "setpiece", "fortress, 136 x 129 m, 15 beds, 1894 wall pieces", "Mistlands stronghold", "8283 pieces; 464 signs + 448 itemstands; 1 discarded row"),
    "god-test.blueprint": ("defence", "setpiece", "stone keep, 29 x 27 x 36 m", "Swamp / Mountain strongpoint", "3356 pieces despite the throwaway name"),
    "gold_castle.blueprint": ("base", "setpiece", "castle, 64 x 73 x 83 m", "reference only", "18036 pieces, of which 2177 are blackforge_ext1 anvils used as metal wall cladding -- a ZDO hazard AND an unintended crafting-level bonus"),
    "halvar-master-refinery.blueprint": ("production", "module", "smelting and refinery yard, 22 x 24 m, 20 stations", "ore country -- Black Forest copper or Mountain silver", "917 pieces; author's own note says '10 x 7'"),
    "hiccup-simplewoodbeambridge.blueprint": ("bridge", "module", "wood beam bridge, 39 x 37 m diagonal span", "Meadows / Black Forest stream crossing", "196 pieces, PURE Meadows-tier -- placeable on day one"),
    "jaik-mistvale-keep.blueprint": ("base", "setpiece", "keep, 28 x 27 x 48 m, 1595 wall pieces", "Mountain / pre-moder -- already selected", "3472 pieces"),
    "long-bridge.blueprint": ("bridge", "setpiece", "ashwood long bridge, 89 m span", "Ashlands / Deep North crossing", "814 pieces; 8 snap points; needs ashwood"),
    "magic-tower.blueprint": ("flavour", "setpiece", "wizard tower, 17 x 17 x 37 m", "Mistlands landmark", "5895 pieces of which 1853 are mist torches -- SEVERE ZDO hazard"),
    "meadhall.blueprint": ("base", "setpiece", "great mead hall, 89 x 89 x 62 m", "capital settlement", "20213 pieces; 360 itemstands; 0.01% missing"),
    "ragnar-warehouse.blueprint": ("production", "empty", "0-byte -- warehouse", "-", "re-download needed"),
    "s-ren-dockhouse.blueprint": ("dock", "setpiece", "harbour with signal fire, 36 x 43 m, berths 4-6 longships", "sheltered bay, any biome but Plains per author", "1225 pieces; author's note: 'Dock with Signal Fire. 4-6 Big Ships.'"),
    "salty-dick-cottage-final.blueprint": ("base", "module", "cottage, 26 x 23 m", "Meadows / Black Forest village", "877 pieces; intact only in the bp_old copy"),
    "salty-dick-mordach-castle-final.blueprint": ("base", "empty", "0-byte -- castle", "-", "re-download needed"),
    "salty-dick-tower-test.blueprint": ("outpost", "empty", "0-byte -- tower", "-", "re-download needed"),
    "serpent.blueprint": ("flavour", "empty", "0-byte -- serpent sculpture", "-", "re-download needed"),
    "shop.blueprint": ("flavour", "setpiece", "market hall, 51 x 54 m, 163 itemstands", "trader settlement", "4131 pieces; 0.61% missing, mostly mod-only decor"),
    "skybound.blueprint": ("base", "setpiece", "vertical ashwood complex, 15 x 50 x 35 m", "cliff-face or skybridge site", "3702 pieces; unusually narrow footprint for its height"),
    "small_long_house.blueprint": ("base", "setpiece", "longhouse, 50 x 34 m, 4 beds", "Meadows / pre-eikthyr -- already selected", "1444 pieces"),
    "smallstarthome.blueprint": ("base", "module", "starter home, 22 x 16 m", "Meadows starter alternative", "1013 pieces"),
    "speeds-bridge2.blueprint": ("bridge", "module", "single-span footbridge, 26 m, 4 m wide", "ANY ravine or stream, stamp as often as needed", "63 pieces from 3 prefabs -- the most repeatable file in the corpus"),
    "vcastle.blueprint": ("base", "setpiece", "castle, 144 x 162 x 135 m", "reference only", "28297 pieces; 102 Pickable_DragonEgg hand out free progression"),
    "wagon-camp.blueprint": ("outpost", "module", "trader wagon camp, 14 x 10 x 5 m, bed + crates", "roadside rest stop -- already selected pre-kall", "160 pieces; 7 snap points; the best rest-stop stamp available"),
    "you-died-ocean-base.blueprint": ("portal", "setpiece", "ocean platform with 14 portals", "portal hub -- teleportall is ON, so this is unusually valuable here", "3400 pieces; 14 portals is 3x any other file"),
}


def sha256(path: Path) -> str:
    if path.stat().st_size == 0:
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> list[dict]:
    by_name: dict[str, list[Path]] = {}
    for directory in CORPUS_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in (".blueprint", ".vbuild"):
                by_name.setdefault(path.name, []).append(path)

    unknown = sorted(set(by_name) - set(CATALOGUE))
    if unknown:
        raise SystemExit(
            "CATALOGUE is incomplete -- these corpus files carry no curated verdict:\n  "
            + "\n  ".join(unknown)
        )

    vanilla, mod_owner = load_evidence()
    rows: list[dict] = []
    for name in sorted(by_name):
        # Prefer the larger copy: 12 files are 0-byte in one directory and
        # intact in the other.
        path = max(by_name[name], key=lambda p: p.stat().st_size)
        size = path.stat().st_size
        parsed = parse_blueprint(path)
        pieces = parsed["pieces"]
        counts = Counter(prefab for prefab, _x, _y, _z in pieces)
        hist = tier_histogram(counts)
        tier, tier_pieces = top_tier(hist)
        if pieces:
            xs = [p[1] for p in pieces]
            ys = [p[2] for p in pieces]
            zs = [p[3] for p in pieces]
            footprint = [round(max(xs) - min(xs), 1), round(max(zs) - min(zs), 1), round(max(ys) - min(ys), 1)]
        else:
            footprint = [0.0, 0.0, 0.0]
        category, kind, role, fit, note = CATALOGUE[name]
        spam = {
            prefab: n
            for prefab, n in counts.items()
            if n >= SPAM_FLOOR and any(h in prefab.lower() for h in SPAM_HINTS)
        }
        viability = resolve_viability(counts, vanilla, mod_owner)
        if kind == "exploit":
            viability["verdict"] = "EXPLOIT/" + viability["verdict"]
        rows.append(
            {
                "file": name,
                "source": str(path),
                "format": parsed["fmt"],
                "bytes": size,
                "sha256": sha256(path),
                "blueprint_name": parsed["meta"].get("name", ""),
                "creator": parsed["meta"].get("creator", ""),
                "licence": "none stated",
                "origin": "fleet corpus (local disk)",
                "committed_body": False,
                "category": category,
                "kind": kind,
                "role": role,
                "fit": fit,
                "note": note,
                **viability,
                "pieces": len(pieces),
                "distinct_prefabs": len(counts),
                "footprint_xzy": footprint,
                "top_tier": tier,
                "top_tier_pieces": tier_pieces,
                "tier_histogram": dict(sorted(hist.items(), key=lambda kv: TIER_ORDER.index(kv[0]))),
                "stations": sum(counts[s] for s in STATIONS if s in counts),
                "portals": sum(counts[p] for p in PORTALS if p in counts),
                "beds": sum(counts[b] for b in BEDS if b in counts),
                "wards": sum(counts[w] for w in WARDS if w in counts),
                "snap_points": parsed["snap_points"],
                "terrain_height_rows": parsed["terrain_height"],
                "terrain_paint_rows": parsed["terrain_paint"],
                "ih_discarded_rows": sum(parsed["discarded"].values()),
                "ih_discarded_shapes": dict(parsed["discarded"]),
                "unknown_sections": parsed["unknown_sections"],
                "pieces_after_unknown": parsed["pieces_after_unknown"],
                "legacy_terrain_rejected": parsed["legacy_terrain"],
                "zdo_spam": dict(sorted(spam.items(), key=lambda kv: -kv[1])),
            }
        )
    return rows


TSV_COLUMNS = (
    "file", "category", "kind", "verdict", "format", "pieces", "distinct_prefabs",
    "footprint", "top_tier", "top_tier_pieces", "missing_pct", "stations", "portals",
    "beds", "snap_points", "ih_discarded_rows", "creator", "licence", "role", "fit", "note",
)


def tsv(rows: list[dict]) -> str:
    out = ["\t".join(TSV_COLUMNS)]
    for r in rows:
        fx, fz, fy = r["footprint_xzy"]
        cells = []
        for col in TSV_COLUMNS:
            if col == "footprint":
                cells.append(f"{fx}x{fz}x{fy}")
            else:
                cells.append(str(r[col]).replace("\t", " ").replace("\n", " "))
        out.append("\t".join(cells))
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="show", help="table to stdout, write nothing")
    ap.add_argument("--json", default=str(HERE / "data" / "corpus_roles.json"))
    ap.add_argument("--tsv", default=str(HERE / "data" / "corpus_roles.tsv"))
    args = ap.parse_args()

    rows = collect()
    if args.show:
        sys.stdout.write(tsv(rows))
        return 0

    tally = Counter(r["category"] for r in rows)
    kinds = Counter(r["kind"] for r in rows)
    payload = {
        "generated_by": "tools/jumpstart/library/classify.py",
        "policy": (
            "REFERENCE ONLY. No licence is stated for any corpus file by any author, so no "
            "body is vendored. Numeric fields are MEASURED by parsing the file; category, "
            "kind, role, fit and note are CURATED human verdicts."
        ),
        "corpus_dirs": [str(d) for d in CORPUS_DIRS],
        "category_tally": dict(sorted(tally.items())),
        "kind_tally": dict(sorted(kinds.items())),
        "entries": rows,
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")
    Path(args.tsv).write_text(tsv(rows), "utf-8")
    print(f"{len(rows)} files -> {args.json}, {args.tsv}")
    print("by category:", dict(sorted(tally.items())))
    print("by kind:    ", dict(sorted(kinds.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
