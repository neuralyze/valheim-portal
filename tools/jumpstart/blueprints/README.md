# Jumpstart base blueprints

Tiered base placement for the Ulfsland jumpstart. Claims are marked
**MEASURED** (observed on this box) or **INFERRED**.

## The headline: placements are solved, not hard-coded

A blueprint coordinate is **seed-specific**. The flat coastal meadow that suits
a Meadows starter hall in one seed is open ocean in the next, so a list of
coordinates is worthless the moment the world is re-rolled.

So `worlds/<World>/<preset>/placements.yaml` never treats a coordinate as the
source of truth. It records a **requirement** — biome, footprint, flatness,
minimum freeboard above sea level, coastal or not, which ZoneSystem anchor to
sit near, what feature the spot must serve — and one command turns every
requirement in the tree into concrete coordinates for whatever seed the world is
rolled onto:

```bash
# 1. terrain grid + placed-content dump for the seed (sibling harness)
printf 'Pirate68\n' > /tmp/seed.txt
VH_SRC=/media/big4/projects/game/valheim/Ulfsland/data/bepinex \
SEEDS=/tmp/seed.txt OUT=/tmp/g SANDBOX=/tmp/my_sandbox STEP=8 HEIGHT=1 \
  tools/seedscan/run_scan.sh                 # ~15 s, 34 MB
VH_SRC=... SEEDS=/tmp/seed.txt OUT=/tmp/loc \
  tools/seedscan/run_locscan.sh              # ~60 s

# 2. re-solve every placement in the world
tools/jumpstart/blueprints/solve_placements.py \
  --world tools/jumpstart/worlds/Ulfsland --seed Pirate68 \
  --grid /tmp/g/00000.biome --locations /tmp/loc/<hash>.json --write
```

That is the deliverable that survives a re-roll. 13 placements across 9 presets
re-solve in **109 s** (MEASURED). `--label alternative` records a seed that is
not live under `solved_alternatives[<seed>]` instead of overwriting `solved`.

The solver refuses to run if `--seed` disagrees with the seed baked into the
grid file, because mismatched coordinates are worse than none.

## Compatibility: can Infinity Hammer read PlanBuild blueprints?

**Yes.** PlanBuild is not installed on this fleet — only its stale
`marcopogo.PlanBuild.cfg` remains — but only the file *format* matters.
All MEASURED from `InfinityHammer.dll` 1.83.0 (disassembled with `monodis`
against the game's own `assembly_valheim.dll` and BepInEx core):

* `HammerBlueprintCommand::LoadFiles` enumerates `*.blueprint` **and**
  `*.vbuild` with `SearchOption.AllDirectories`, from **both**
  `<game>/BepInEx/config/<folder>` and `Paths.ConfigPath/<folder>`.
* `<folder>` is the config entry *"6. Blueprints" / "Blueprint folder"*, whose
  default value is the literal string **`PlanBuild`**. Infinity Hammer reads the
  very directory PlanBuild uses — this is deliberate interop, not a coincidence.
* `GetPlanBuild` / `GetPlanBuildObject` parse the PlanBuild header keys
  `#Name:`, `#Creator:`, `#Description:`, `#Category:`, `#Center:`,
  `#Coordinates:`, `#Rotation:`, `#SnapPoints`, `#Pieces` (case-insensitive),
  plus Infinity Hammer's own `#TerrainHeight:` / `#TerrainPaint:`.
* Its changelog corroborates the intent: v1.79 and v1.80 both list *"Improves
  compatibility with PlanBuild mod"*.
* An unknown extension throws `Unknown file format.`; the legacy
  `#Height`/`#Paint` terrain sections are explicitly rejected with *"Legacy
  #Height/#Paint terrain format is no longer supported."*

Proof beyond the strings: **112 files from the fleet's own corpus parse
correctly** with a reader written from that disassembly, and the `.vbuild` field
layout I derived empirically is confirmed by Infinity Hammer's own
`GetBuildShareObject` — see *File formats* below.

### Server-side or admin-client only?

**Admin-client only, and that matches the operator's rule anyway.** MEASURED:
`hammer_restore`'s handler calls `InfinityHammer.Hammer::Equip()` as its first
action; `Equip` calls `ServerDevcommands.Helper::GetPlayer()`, searches that
player's inventory for a hammer, throws `Unable to find the hammer.` if there is
none, and otherwise calls `Humanoid::EquipItem`. There is no headless path
through Infinity Hammer. All building happens from the `ulfsland-admin` seat,
which is exactly the intended workflow.

For placement with **nobody online**, use `to_rcon_plan.py` (below).

## The two placement routes, and when each is right

| | Admin client (Infinity Hammer) | Headless (ValheimRcon `spawn`) |
|---|---|---|
| Command | `hammer_blueprint <file>` then place, or `hammer_restore <file>` at its saved position | one `spawn` per object, from `to_rcon_plan.py` |
| Fidelity | full — quaternion rotation, scale, object **data** (chest contents, sign text, ward permissions) with `data=true` | position + rotation only |
| Needs a player | **yes** — hammer equipped | **no** |
| Terrain | can apply the blueprint's captured `#TerrainHeight` / `#TerrainPaint` | none |
| WearNTear support | computed normally by the game | **not computed** — ghost-init writes the ZDO directly |
| Use when | the operator is building or fine-tuning interactively | seeding a freshly re-rolled world before anyone joins |

MEASURED, `spawn` resolves the prefab from `ZNetScene`, instantiates it, then
calls `ZNetView::FinishGhostInit()` and `Object::Destroy` — a persistent ZDO
with no live GameObject, which is why it works with an empty server.

### Deploying to the admin client

Infinity Hammer uses the **containing folder name as the blueprint's category**
(MEASURED: `GetFolderNameFromPath`). So copy a preset's directory across whole:

```
<preset>/blueprints/*  ->  <client>/BepInEx/config/PlanBuild/<preset>/
```

and the whole preset appears as one category in `hammer_menu blueprints`.

## Rotation convention

`placements.yaml` records rotation as `{units: euler_degrees_yxz, yaw, pitch, roll}`.

MEASURED: ValheimRcon's `spawn ... -rotation <x> <y> <z>` passes the vector to
`UnityEngine.Quaternion::Euler`, which composes as `Ry(y) * Rx(x) * Rz(z)` —
the YXZ convention. Yaw is the **Y** component, in degrees, and `+yaw` turns the
building clockwise viewed from above. Only yaw is used in the placements; pitch
and roll stay 0 so buildings sit level.

`findObjects -detailed` reports rotation the same way — `Quaternion.eulerAngles`
formatted `0.##` — so the two agree.

Blueprints store a full **quaternion**, so `to_rcon_plan.py` converts. That
conversion is validated, not assumed: quaternion → Unity euler → quaternion
round-trips to a worst error of **3.4e-6 degrees** over 300 000 random
quaternions, over every exact gimbal-lock case, and over the 22.5-degree grid
Valheim building actually uses. (The gimbal-lock fold is a real trap: a naive
`asin` decomposition silently produces a ~52-degree error on roughly one
rotation in twenty thousand.)

## Origin alignment

Corpus blueprints disagree about their own origin: some are corner-origin with
their minimum at `(0,0,0)`, some are centred, and their lowest piece sits
anywhere from **−42.45** to **0** on Y (`PuP_black_house_full` has a 42 m
basement). So a bare coordinate is ambiguous.

`align: ground-center` (the default) centres the blueprint on the target X/Z
and drops its **lowest** piece exactly onto the target Y. `raw`, `ground` and
`center` are also available.

## Coordinates and sea level

`y` is terrain height in world units, and **sea level is y = 0** — not the 30 of
Valheim's in-game water plane. MEASURED: `WorldGenerator` heights on the
Pirate68 grid put Ocean-biome cells at −400..+4 with median exactly 0.0, and a
regression of the 12 301 ZoneSystem location instances against the grid gives
`loc.y = 1.0003 * genheight − 0.53`, i.e. the same units with no offset.

Grid orientation was verified the same way rather than assumed: `grid[z, x]`
agrees with the location dump's own biome field on **99.90 %** of 11 951
single-biome instances; `grid[x, z]` agrees on 16.82 %.

## Site scoring

`site_finder.py` scores a candidate footprint on:

* **flat** — max-minus-min terrain height over the window; this is how much hoe
  work the site needs.
* **biome purity** — fraction of footprint cells in the requested biome.
* **freeboard** — `min_height_m`, default 2 m. Dryness alone is not enough: a
  dead-flat 0.05 m site is the waterline, and the first solve produced exactly
  that before this was added.
* **water distance** — via a Euclidean distance transform of the water mask.
  The naive per-candidate scan over 2.3 M water cells turned a one-second query
  into minutes.
* **location clearance** — default 60 m from every placed ZoneSystem instance.
  ZoneSystem re-cuts terrain inside a location's clear area when it spawns, so a
  site that reads flat now will fight the generator later. The dump carries no
  exterior radius (MEASURED: its fields are name/prefab/biome/group/unique/
  icon/x/y/z/placed), so this is a conservative fixed stand-off, **INFERRED**
  rather than per-prefab exact.
* **intra-preset exclusion** — each solved site excludes the next by 1.5
  footprints. Presets do *not* exclude each other: they are alternative world
  states that never coexist, so two presets resolving to the same meadow is
  correct. Without intra-preset exclusion, `pre-kall`'s two placements resolved
  to the identical cell and the camp landed inside the portal shelter.

When a requirement cannot be met at its stated tolerance the solver widens
`max_flat` and `within` up to 4x/6x and **records which relaxation it used** in
the `relaxation` field, rather than failing silently or pretending the site was
exact.

## File formats, both MEASURED from InfinityHammer.dll

`.blueprint` piece row — `;`-separated, any `,` rewritten to `.` first so
decimal-comma locales parse, floats invariant `0.###`:

| # | field | # | field |
|---|---|---|---|
| 0 | prefab name | 9 | extra info / text (default `""`) |
| 1 | PlanBuild category — parsed, unused; IH writes `""` | 10–12 | scale x, y, z (default 1) |
| 2–4 | position x, y, z | 13 | base64 ZDO data (only with `data=true`) |
| 5–8 | rotation quaternion x, y, z, w | 14 | placement chance (default 1) |

IH writes the row as `prefab;;x;y;z;rx;ry;rz;rw;info;sx;sy;sz;data` — note the
empty category column and that it does not emit `chance`.

`.vbuild` (legacy BuildShare) — split on a **single space with empty entries
KEPT**, so the layout is fixed-arity and a zero component serialises as an empty
field:

| # | field |
|---|---|
| 0 | prefab name |
| 1–4 | rotation quaternion x, y, z, w (default 0) |
| 5–7 | position x, y, z (default 0) |
| 8 | extra info | 
| 9 | placement chance (default 1) |

There is no scale column; IH forces `Vector3.one`.

**Splitting `.vbuild` on whitespace *runs* is the trap** — it drops the empty
fields and shifts every column. I derived this layout empirically before finding
IH's parser, and validated it independently: across 12 137 rows the four
rotation components satisfy `x²+y²+z²+w² = 1` to within **1.5e-6**. Splitting on
runs instead silently discarded 1 615 of `berserkers-keep.vbuild`'s 10 686
pieces and produced a nonsense 10 152 m vertical footprint.

## Viability: which blueprints actually work on 1.0.12

The real risk with community blueprints is that a file authored for 0.217
references pieces that were renamed or removed, or belongs to a build mod this
fleet does not have. So every prefab name in the corpus is resolved against
evidence rather than judged by eye:

```bash
./scan_piece_prefabs.py --out data/piece_prefabs.json    # ~97 s
./inventory.py <corpus dirs...> --json data/inventory.json \
    --prune-evidence data/prefab_evidence.json
```

`scan_piece_prefabs.py` harvests **668 073** prefab-shaped tokens from the 796
UnityFS bundles in `valheim_server_Data/StreamingAssets/SoftRef/Bundles` (the
bundle reader is reused, unmodified, from the sibling `extract_prefab_names.py`),
plus the tokens unique to each of the 95 deployed mod plugin directories. That
distinguishes *removed by Iron Gate* from *supplied by a mod we happen to have*.

Verdicts across all 112 files:

| verdict | count |
|---|---|
| `PLACES_CLEAN` — every prefab resolves | **88** |
| `PLACES_WITH_GAPS` — <1 % of objects missing | 12 |
| `EMPTY` — 0-byte truncated download | 12 |

Only **31 distinct** prefab names in the whole corpus fail to resolve, and they
are almost all from build mods this fleet lacks — `MarketPlaceNPC`,
`MS_PressurePlate_BlackMarble`, `emberwood_pillar4_bal`, `MMFrostCannon`,
`BasicTurret`, `Odins_Alchemy_Book`, `opalchemy`, `opcauldron`, `h_chain`,
`h_window_0x`, `Hayze_gate_0x`, `$custompiece_*`, `Totem2`/`Totem3`,
`piece_Sundial`, `Thorward`.

The evidence set discriminates correctly rather than answering yes to
everything: fabricated names (`wood_wall_zzz`, `grausten_flimflam`,
`blackmarble_notreal_9x9`, `piece_totally_fake`) are all absent, while real
1.0-era Ashlands and Mistlands pieces (`Piece_grausten_pillarbeam_medium`,
`Piece_flametal_pillar`, `ashwood_wall_2x2`, `crystal_wall_1x1`,
`piece_dvergr_metal_wall_2x2`, `dvergrprops_bed`) are all present.

One trap worth recording: `wood_wall_log_4x0.5` is the **single most common
piece in the entire corpus** (1 135 objects) and a token regex without `.` in
its character class reports it as removed. Dotted prefab names get a second
scanning pass for exactly this reason.

## The tiered shortlist

Materialise with `./materialise.py --world ../worlds/Ulfsland --all --strict`.

| Preset | Blueprint | Author | Pieces | Footprint x·z·y m | Stations it brings | Verdict |
|---|---|---|---|---|---|---|
| `pre-eikthyr` | `small_long_house` | Пипкин | 1 444 | 50·34·15 | workbench, cooking, chests — **deliberately no forge** | CLEAN |
| `pre-elder` | `fox-0123456` | Fox- | 1 229 | 47·39·12 | forge, kiln, smelter, beehive, cauldron, cooking, workbench, portal | CLEAN |
| `pre-elder` | `dock` | Olivanderr | 2 036 | 42·29·24 | harbour, chests | CLEAN |
| `pre-bonemass` | `BjOrN_blueprint001` | BjOrN | 1 908 | 66·66·17 | forge, smelter, kiln, stonecutter, cauldron, prep, portal, workbench | GAPS 0.05 % (`piece_Sundial` ×1) |
| `pre-moder` | `jaik-mistvale-keep` | Jaik | 3 472 | 28·27·**48** | bed, chest, cooking, forge, oven, prep, portal, workbench | CLEAN |
| `pre-yagluth` | `cozy_house` | Пипкин | 6 034 | 52·53·24 | **windmill, blast furnace**, artisan, forge, smelter, kiln, oven, cauldron, cooking | CLEAN (black forge dropped) |
| `pre-queen` | `PuP_black_house_full` | PuP | 6 262 | 41·73·78 | **black forge**, blast furnace, galdr table, artisan, smelter, spinning wheel, oven, prep | CLEAN |
| `pre-fader` | `deardly-casa-drly` | Deardly | 1 901 | 31·30·13 | black forge, eitr refinery, galdr table, artisan — grausten/ashwood/flametal | CLEAN |
| `pre-kall` | `Portal13.vbuild` | — | 133 | 4·7·7 | portal shelter | CLEAN |
| `pre-kall` | `wagon-camp` | Cooties | 160 | 14·10·5 | cauldron, cooking, barrel, chests — **no forge** | CLEAN |
| `deepnorth-sandbox` | `Ultimate_OutPost` | Randominis | 1 820 | 53·49·14 | **every station in the game** (see below) | CLEAN |
| `deepnorth-sandbox` | `god-portaaoo` | God | 2 191 | 36·12·23 | stone portal arcade | CLEAN |
| `deepnorth-sandbox` | `dock` | Olivanderr | 2 036 | 42·29·24 | harbour | CLEAN |

`Ultimate_OutPost` is the find of the corpus: at only 1 820 pieces it carries
workbench, forge, stonecutter, artisan table, black forge, blast furnace,
smelter, charcoal kiln, windmill, spinning wheel, eitr refinery, galdr table,
oven, prep table, cauldron, cooking station, beehive, barber, a portal and 32
chests — and resolves **100 % clean** with zero missing prefabs.

Tier bleed is handled rather than tolerated: community bases tend to ship the
whole station set, so `cozy_house` would hand a Plains-tier party a Mistlands
black forge. `drop_prefabs` in the placement strips named prefabs
(`to_rcon_plan.py --drop-prefab`).

### What I would NOT use, and why

* `15643-Valheimian`, `14464-Valheimian`, `gnome_house` — **loot-spam
  exploits**, 361–491 `TreasureChest_*` objects each. Not bases.
* `vcastle` (102 `Pickable_DragonEgg`) and `PuP_megaCastle` (28 core stands) —
  place cleanly but hand out free progression.
* `citadel` (33 655 pieces), `PuP_megaCastle` (25 207), `vcastle` (28 297),
  `gold_castle` (18 036), `meadhall` (20 213) — too large. At one RCON `spawn`
  per object these are 18 000–34 000 commands, and `citadel` brings no stations
  at all.
* `s-ren-dockhouse` (22 % missing), `long-bridge` (19 %), `PuP_Capitol` (7.7 %),
  `small_long_house` in its `old_Storgard` copy, `PuP_house13`, `PuP_house11`,
  `bridge.vbuild`, `altar` — these were the pre-fix `BROKEN` set; after the
  dotted-prefab correction all but their genuine mod dependencies resolve. The
  remaining true failures are mod-piece dependencies, not version rot.
* The 12 zero-byte files.
* `halvar-master-refinery` — a better *thematic* iron workshop than the one
  chosen, but it ships an artisan table, which needs a Dragon Tear and so leaks
  a tier into `pre-bonemass`.

## Downloaded blueprints vs build-once-and-export: the honest comparison

The parent's leading option was to hand-build one base per tier from the admin
seat and replay it as data. Having measured both:

**Neither is what I would recommend. The corpus already on disk beats both**,
and the export machinery is worth building anyway — because it is the same
machinery.

| | Downloaded blueprints | Build-once-and-export |
|---|---|---|
| Cost to obtain | Zero, already on disk: 100 non-empty files | ~1–2 h of admin-seat building **per tier**, so 9–18 h |
| Licence | **No licence stated anywhere.** Local-use only, cannot be vendored | Fully ours |
| 1.0 viability | MEASURED: 88/112 place clean | Clean by construction |
| Quality | Genuinely good community builds, including post-Ashlands grausten/flametal work | Whatever the operator builds |
| Tier fit | Approximate; needs `drop_prefabs` to stop tier bleed | Exact |
| Export fidelity | n/a | `hammer_save` writes full quaternions at `0.###`, plus scale, extra info and optional base64 object data — **lossless** |

The decisive finding is about the *export channel*, and it inverts the premise
of the question. `findObjects`/`spawn` is **not** a good round-trip:

* `findObjects -detailed` reports position as `({0:0.##} {1:0.##} {2:0.##})` —
  1 cm quantisation — and rotation as `Quaternion.eulerAngles`, also `0.##`.
  Euler is a lossy, ambiguous encoding to re-derive a quaternion from.
* It emits **one line per object with no pagination**, and MEASURED,
  `ValheimRcon.Core.RconCommandReceiver::ValidatePayloadLength` hard-truncates
  every response at **4050 payload bytes**. A detailed line is ~120 bytes, so
  about **32 objects per response** — against a 1 820-piece base. It also scans
  all of `ZDOMan` on the main thread, which is consistent with the two
  observed server wedges.
* `spawn` cannot set scale, cannot set object data, and never sets
  `Piece.m_creator`.

Whereas `hammer_save` on the admin client writes the *same PlanBuild format* the
corpus uses, at full fidelity. So "build once and export" and "use downloaded
blueprints" converge on one artefact: **a `.blueprint` file**. The right pipeline
is therefore:

1. Take a corpus base as the starting point — it is free and it works.
2. Fix it up from the `ulfsland-admin` seat: strip out-of-tier stations, stock
   the chests, adjust for the actual site.
3. `hammer_save <name> data=true` — now it is **our** file, licence-clean,
   lossless, and the fleet owns it.
4. Replay it either interactively (`hammer_blueprint`) or headlessly
   (`to_rcon_plan.py` → RCON `spawn`, one command per object, bottom-up).

Step 3 is what retires the licence problem permanently, and it costs minutes per
tier instead of hours, because step 1 did the building.

`to_rcon_plan.py` emits pieces in **ascending Y** so that each piece's support
already exists beneath it — necessary because ghost-init writes the ZDO without
running WearNTear support propagation, so an unsupported piece can be destroyed
when the zone first loads. Scale loss is negligible in practice: MEASURED, the
shortlist blueprints carry non-unit scale on 0–24 objects out of thousands.

### Stocking chests headlessly

RCON `spawn` cannot pre-fill a container, but ValheimRcon has the rest of the
chain:

```
findObjects -prefab piece_chest -near <x> <y> <z> <r> -detailed
  -> -Prefab: <name> Id: <id>:<userid> Position: (x y z) Zone: (zx zy) Rotation: (ex ey ez)Creator: <n> Health: <h>
addItemToContainer <id:userid> <item> -count <n> [-quality q] [-variant v] [-durability d] [-force]
showContainer <id:userid>   removeItemFromContainer ...   clearContainer ...
```

Keep the `findObjects` filter tight — the 4050-byte cap applies.

## Files

```
scan_piece_prefabs.py   harvest prefab-name evidence from bundles + mod DLLs
inventory.py            parse, measure and rate blueprints; flags loot payloads
build_manifest.py       identity + verdict manifest for the corpus
materialise.py          copy-from-source with checksum verification
site_finder.py          solve one placement requirement against a seed
solve_placements.py     re-solve every placement in a world, one command
to_rcon_plan.py         blueprint -> RCON `spawn` plan (headless placement)
PROVENANCE.md           authors, licence position, exploit payloads
data/corpus_manifest.*  the committed manifest (json + tsv)
data/inventory.json     full per-file measurements
data/prefab_evidence.json  resolution for every prefab the corpus uses
data/piece_prefabs.json 28 MB evidence cache -- gitignored, regenerate on demand
```

Per-preset placement data lives in
`tools/jumpstart/worlds/Ulfsland/<preset>/placements.yaml`, with bodies
materialised into `<preset>/blueprints/` and gitignored there.
