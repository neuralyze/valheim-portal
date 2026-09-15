# Valheim jumpstart presets

Tiered fast-forward presets for the **Ulfsland** experimental world (Valheim
1.0.12, network 40). One preset per boss tier, each describing the progression
state a party would plausibly be in *immediately before* that boss, plus a final
sandbox preset that hands over the whole chain including Deep North.

```
presets/*.yaml               versioned data, one file per tier
jumpstart.py                 driver: list / show / verify / apply / itemset
extract_prefab_names.py      rebuilds data/prefab_index.json from the game install
data/prefab_index.json       prefab-name evidence snapshot used by `verify`
worlds/                      per-world, per-preset INSTANTIATION of these presets
```

Everything in `presets/` is data. `jumpstart.py` never executes anything out of
a preset file; it reads it, plans commands, and refuses to run unverified prefab
names.

`presets/` is world-agnostic: a preset says what a tier *is*. `worlds/` says what
that tier *becomes* in a particular world — the server-config overrides it
implies, its ServerCharacters starting kit, its global keys, and the blueprints
and coordinates that build it. Everything under `worlds/<World>/<preset>/` is
generated from `presets/<preset>.yaml` by `worlds/derive.py`, which imports this
directory's own `load_preset`, so the two cannot drift. See `worlds/README.md`.

Claims below are marked **MEASURED** (observed on this box) or **INFERRED**.

---

## The presets

| Preset | Biome / boss | One line |
|---|---|---|
| `pre-eikthyr` | Meadows / Eikthyr | Flint and leather, no metal, **no pickaxe at all** — Hard Antler only drops from Eikthyr. No global keys. |
| `pre-elder` | Black Forest / The Elder | Bronze kit, antler pickaxe, Stagbreaker, Ancient Seeds in the bag, forge + smelter + kiln. |
| `pre-bonemass` | Swamp / Bonemass | Full iron, root armour, poison mead, Crypt Keys for re-farming; no Wishbone, so no silver. |
| `pre-moder` | Mountain / Moder | Silver and wolf sets, Fenring armour, frost mead, Wishbone in hand; no Dragon Tears, so no artisan table. |
| `pre-yagluth` | Plains / Yagluth | Black metal and padded, Lox cape, Plains farm stock, Goblin Totems, blast furnace + windmill + spinning wheel. |
| `pre-queen` | Mistlands / The Seeker Queen | Carapace and mage sets, Eitr, the four Mistlands staffs, Demister, black forge level 2. |
| `pre-fader` | Ashlands / Fader | Flametal and Ashlands medium sets, Ashlands staffs, Asksvin saddle, black forge level 3. |
| `pre-kall` | Deep North / Kall (the Frozen King) | Post-Fader: **Fader's Relic, Embers, Ember Charges, Seal Pelts, Frostcores, Grappling Hook**, frost mead + frost capes, **black forge level 4**. Kall's keys deliberately unset. |
| `deepnorth-sandbox` | Deep North / Kall defeated | The whole chain including `defeated_frozenking_p3`, Bloodgold arsenal, Deep North armour, black forge level 6, gathering skills at 100. |

`jumpstart.py list` prints the same table; `jumpstart.py show <preset>` expands one.

### Fields in a preset

* **`global_keys`** — cumulative `defeated_*` keys for bosses already dead at
  that point. `pre-eikthyr` has none; each later preset adds exactly one.
* **`world_modifiers`** — the `-modifier <key> <value>` pairs the preset
  implies. **Not applied by the driver**: these are process launch arguments, so
  they belong in `valheim.env`'s `SERVER_ARGS`. The driver prints the exact line.
* **`kit`** — worn/carried gear, tools, ammunition, consumables, as
  `{prefab, quality, count}`.
* **`materials`** — biome material stock. This doubles as the **recipe-unlock
  mechanism**: Valheim marks a recipe known once the player has held its
  materials, so a full material stock is what makes the crafting menu look
  lived-in rather than empty.
* **`skills`** — per-skill target levels (see the curve below).
* **`stations`** — station prefab plus the **level** it must reach. Level is
  `1 + placed extension pieces`, so each entry lists the exact extensions.
* **`chain_items`** — Ashlands/Deep North progression inputs, separated from
  `materials` because they are what actually gates the next biome. Present on
  `pre-kall` and `deepnorth-sandbox` only.
* **`notes.withholds` / `notes.gaps`** — what the preset deliberately does not
  give, and what is known-unresolved.

### Counts are bounded by WEIGHT, not just by slots

A Valheim character carries 300 kg — MEASURED from `assembly_valheim.dll`:
`Player..ctor` sets `m_maxCarryWeight = 300f`, `GetMaxCarryWeight()` multiplies
it by `Game::m_carryWeightRate` (1 unless a global key moves it), and
`IsEncumbered()` is `Inventory.GetTotalWeight() > GetMaxCarryWeight()`. Nothing
is equipped on spawn and a new character has no status effects, so 300 kg is the
whole budget.

Slots alone therefore do not bound a kit. On 2026-09-15 `pre-bonemass` rendered
30 prefabs into 32 of 32 slots weighing **426.8 kg**, and the operator found it
by being unable to walk away from the spawn. `worlds/derive.py` now refuses to
render a template over `inventory.carry_budget_fraction` of that capacity, and
per-prefab weights live in `data/item_weights.json` (regenerate with
`extract_item_weights.py`).

The authoring rule that follows, applied to all nine presets:

* A **stackable `kit` or `chain_items` entry** is sized so its stack weighs no
  more than **5 kg**. That is what turns 150 arrows into 50, 20 meads into 5 and
  a 50-stack of `FrostCore` into 5. Every item *class* is kept — the kit still
  teaches the tier — only the depth of each stack comes down. The base has a
  forge and the stock to make more.
* **`materials` counts are left at bulk.** They are the recipe-unlock mechanism
  and their natural home is the placed base's chests, so `derive.py` carries as
  many material *kinds* as the weight budget allows (lightest first, so the
  player gets breadth) and routes the rest to `settings/chest-manifest.yaml`.
* Count-1 entries are exempt: a single iron chestpiece weighs 15 kg and cannot
  be made lighter without removing it.

### World modifiers

MEASURED from `assembly_valheim.dll`: `WorldModifiers = {Default, Combat,
DeathPenalty, Resources, Raids, Portals}` and `WorldModifierOption = {Default,
None, Less, MuchLess, More, MuchMore, Casual, VeryEasy, Easy, Hard, VeryHard,
Hardcore, Most}`.

Every `pre-*` preset asks for `Resources More` and leaves the rest `Default`.
`deepnorth-sandbox` asks for `DeathPenalty Casual`, `Resources MuchMore`,
`Raids None`, `Portals Casual`.

Two caveats:

* Ulfsland currently runs `SERVER_ARGS='-preset Normal'` and there is **no**
  global resource multiplier deployed (MEASURED: the world records
  `resources_default`). The 2x effects that exist today are *skill-gated*:
  Smoothbrain Mining `Mining Yield Factor = 2`, Lumberjacking `Tree item yield
  modifier at level 100 = 2`, Foraging `Foraging Yield Factor = 2`, ImpactfulSkills
  `MiningLootFactor = 2`. Adding `-modifier Resources More` **stacks on top of
  those** — it is a genuine buff, not a restatement of the status quo.
* Whether the two Mining 2x systems stack with each other is UNPROVEN.
* Which option values each modifier key legally accepts is INFERRED from the
  in-game modifier UI; the enum is global, so an illegal pairing is possible.
  `Portals Casual` in particular is INFERRED to mean "no teleport restrictions".

---

## Skill curve

Vanilla skill names are MEASURED from the `Skills.SkillType` enum in
`assembly_valheim.dll` on this exact build:

```
Swords Knives Clubs Polearms Spears Blocking Axes Bows ElementalMagic BloodMagic
Unarmed Pickaxes WoodCutting Crossbows Jump Sneak Run Swim Fishing Cooking
Farming Crafting Dodge Ride
```

`Cooking`, `Farming`, `Crafting` and `Dodge` are new in 1.0 — presets use them.
Mod skills MEASURED from the deployed configs: `Mining`, `Lumberjacking`,
`Foraging`, `Blacksmithing` (Smoothbrain / BlacksmithingExpanded). Smoothbrain
also ships a `Farming` skill whose name now **collides** with vanilla 1.0
`Farming`; presets list `Farming` once, and which implementation receives the
level is INFERRED.

The curve is deliberately **not uniform**. Rules:

1. **A per-tier ceiling, not a flat value.** Roughly 12 / 24 / 38 / 50 / 64 / 74
   / 82 / 90 / 100 across the nine presets. The ceiling is the *movement* skill
   band, because Run/Jump accrue from simply existing.
2. **Movement leads.** `Run` is at the ceiling in every preset (sole highest in
   seven of nine, tied with `WoodCutting` in `pre-elder` and with the other
   maxed lines in the sandbox), then `Jump`, then `Swim`/`Sneak`. These are
   biome-independent: a player at the iron tier has been running for a long
   time regardless of what they fight.
3. **One combat line leads, the rest trail.** The tier's signature weapon sits
   near the ceiling (`Clubs` at the iron tier for the Bonemass mace, `Bows`
   throughout because everyone shoots), secondaries run ~70–80% of that, and
   fringe lines stay low. `Unarmed` never exceeds 30 — nobody punches Valheim
   by accident.
4. **Magic is zero until Mistlands.** `ElementalMagic` and `BloodMagic` are 0
   through `pre-yagluth`, appear small at `pre-queen` (20/10 — a player who has
   just found their first staff), and climb from there. Same for `Crossbows`
   (0 until the Arbalest exists) and `Ride` (0 until Plains has Lox).
5. **Gathering is capped below 100 until the sandbox.** `Mining`,
   `Lumberjacking`, `Foraging` and `Farming` peak at 84/70/60/66 in `pre-kall`
   and only reach 100 in `deepnorth-sandbox`. This is the economically load-bearing
   decision: **level 100 is exactly where the 2x yield mods switch on**, so a
   preset that handed out round 100s would silently double the world's resource
   economy. `deepnorth-sandbox` does that on purpose.
6. **Support skills track settlement, not combat.** `Crafting`, `Cooking`,
   `Blacksmithing` and `Farming` rise with how much base-building the tier
   implies, which is why they jump hardest between `pre-moder` and `pre-yagluth`.
7. **`Fishing` stays low everywhere** (2 → 50). It is optional content and a
   high number would be a lie about how the party played.

Erosion: `dev.crystal.deathpenalty.cfg` has `SkillLossPercent = 5` with
`ResetLevelProgress = true` (MEASURED), so every death shaves 5% off every skill
and wipes in-level progress. Granted skills are not permanent; expect to re-apply.

---

## Automated vs manual

| Thing | Status | How |
|---|---|---|
| Global keys | **AUTOMATED** | RCON `addGlobalKey`, one at a time, with `globalKeys` read back after each. |
| Gear / materials / chain items | **AUTOMATED** | RCON `give <steamid> <prefab> -count N -quality Q`, de-duplicated through a ledger. |
| World modifiers | **LAUNCH** | Printed as a ready-to-paste `SERVER_ARGS` line. The driver never writes `valheim.env`. |
| Skills | **MANUAL** | No working server-side backend on 1.0.12. The driver emits per-character steps plus AdminQoL item-set YAML. |
| Station levels | **MANUAL** | Level comes from extension pieces; an admin must place them in-game. |
| EpicLoot magic items | **IMPOSSIBLE remotely** | See gaps. |

### Driver interface

```
jumpstart.py list
jumpstart.py show <preset>
jumpstart.py verify [preset ...] [--detail]
jumpstart.py itemset <preset> [--out FILE]
jumpstart.py apply <preset> --players <id>[,<id>...] [--dry-run | --commit]
                  [--keys-only | --items-only] [--keep-going]
                  [--world Ulfsland] [--valheim-root DIR]
                  [--rcon-host H] [--rcon-port P] [--rcon-config FILE]
                  [--ledger FILE] [--skill-backend auto|manual|servercharacters]
```

`apply` is a dry run unless `--commit` is passed. There is no way to mutate the
server by forgetting a flag.

Safety properties, all deliberate:

* **The RCON password is never an argument and never embedded.** It is parsed
  out of the deployed `org.tristan.rcon.cfg`, section-aware (that file has two
  distinct `Webhook url` keys, so a flat grep would be wrong), and the
  credentials object's `__str__` prints a character count instead of the value.
* **Keys go one at a time with a read-back.** After each `addGlobalKey` the
  driver re-reads `globalKeys` and aborts if the key is not present, because
  CLLC and ZenWorldSettings both react to key state and a half-applied chain
  must be diagnosable rather than silently wrong.
* **Item grants are idempotent via a ledger.** `give` is additive, so a naive
  re-run would double everything. The ledger (default
  `$XDG_STATE_HOME/valheim-jumpstart/<world>.json`, override with `--ledger`)
  records `(world, preset, player, prefab@quality) -> count`; a re-run grants
  only the shortfall and a fully-applied preset is a no-op.
* **Nothing is silently skipped.** Every RCON response is inspected; anything
  matching a failure marker is recorded as a failure, the run stops (or
  continues with `--keep-going`), and the process exits non-zero with the
  offending command and the server's own words.
* **Unverified prefabs block the run.** `apply` re-runs the index check and
  refuses to start if any prefab in the preset has no evidence.

The Source RCON client in `jumpstart.py` has **not** been exercised against the
live Ulfsland server from this task — a sibling task owns the server and RCON
proof. The framing is INFERRED from Valheim Rcon 1.6.2 being a Source RCON
implementation. The `globalKeys` response parser is deliberately tolerant (it
collects every identifier-shaped token) because a parser that over-collects is
safe here, while one that under-collects would false-alarm on read-back.

### The skill step is pluggable

`SkillBackend` implementations are registered in `SKILL_BACKENDS` and selected
with `--skill-backend` (default `auto`: first available, else manual).

* `ServerCharactersBackend` — **permanently reports unavailable today.**
  `Smoothbrain-ServerCharacters 1.4.16` does not load on 1.0.12: it resolves
  `PlayerProfile.GetCharacterFolderPath(FileHelpers.FileSource)` from
  `Initialize()`, which Harmony runs at `FejdStartup.Awake`, and Valheim 1.0
  moved that member to `SaveSystem` (MEASURED). The plugin therefore throws
  before registering anything and its loopback `WebInterfaceAPI` on
  `127.0.0.1:5982` (`GiveItem`, `RaiseSkill`, `ResetSkill`, `GetPlayerList`,
  `SendIngameMessage`, no auth) never binds. One MemberRef retarget fixes all
  three call sites, but that is an operator decision. Even if something *is*
  listening on 5982 the backend stays disabled until that decision is signed
  off, so an unrelated listener cannot be mistaken for a working backend.
  When it is fixed, only `available()` and `apply()` need bodies.
* `ManualSkillBackend` — always "available", applies nothing, and emits the
  exact hand steps: generate the item set, drop it in the admin client's
  `BepInEx/config/AdminQoL/AdminQoL.ItemSets.yml`, `adminqol_itemsets_reload`,
  `adminqol_itemsets_list`, then `itemset jumpstart-<preset>` per character.

`ReefTeam-ReefCharacters 0.1.0` is 1.0-correct but has no skill commands at all,
so it is not a candidate backend.

---

## Known gaps

1. **No central skill mechanism.** The single biggest gap. AdminQoL's `itemset`
   applies to the **invoking admin's own local character only** (MEASURED), so
   every player has to run it themselves or an admin has to load each character
   in turn. There is no server-side path today.
2. **EpicLoot magic items cannot be granted remotely.** RCON `give` takes a
   plain prefab name; it has no way to express EpicLoot rarity, enchant
   effects or a legendary set. The AdminQoL YAML does support an `epicLoot:`
   block, so the manual path can do it — the automated path never will.
3. **Station levels need an admin in-game.** `knownStations` in an item set
   teaches recipes; it does not place pieces and cannot raise a station's level.
   Every `stations` entry with `level > 1` is a build task. **All building,
   terrain and world modification is done from the `ulfsland-admin` client
   edition** — never from `ulfsland-vr` or `ulfsland-flat`.
4. **World modifiers need a restart.** They are launch arguments. Ulfsland takes
   roughly five minutes from start to joinable.
5. **The ObjectDB evidence snapshot predates 1.0.** See verification below: the
   Deep North item names are verified from 1.0.12 game data and EpicLoot data,
   but *not* from a live 1.0.12 ObjectDB dump.
6. **Frost resistance sourcing is unverified.** `pre-kall` supplies
   `MeadFrostResist` plus `CapeWolf`, `CapeFeather`, `CapeAsh` and `CapeAsksvin`.
   Which of those capes actually carries frost resistance in 1.0.12 is not
   verified here; the mead is the reliable source.
7. **`defeated_moder`, `defeated_hive`, `defeated_serpent`, `defeated_writhan`
   exist in 1.0.12 game data** (MEASURED, alongside the keys the presets use)
   and no preset sets them. Of these, only `defeated_serpent` is referenced by
   any deployed mod config (5 references). Moder is assumed to still set
   `defeated_dragon`, which is the key EpicLoot and the vanilla assembly both
   use — but if 1.0 switched Moder to `defeated_moder`, the Mountain gate would
   need revisiting.
8. **`WorldLevel` is a real global key** (MEASURED in the `GlobalKeys` enum,
   along with `AllRecipesUnlocked`, `NoCraftCost`, `TeleportAll` and friends) and
   no preset touches it. The value grammar for `-setkey WorldLevel` is unknown,
   and 1.0 ties creature scaling to world level, so guessing here would be
   actively harmful.

---

## Side effects of setting global keys

Setting a `defeated_*` key is **not** cosmetic on this server. All MEASURED from
the deployed configs:

* **CLLC (CreatureLevelAndLootControl)** has `Second factor = BossesKilled`,
  `Difficulty = Medium`, max Five stars. Every key added raises the creature
  level curve **world-wide, immediately**, including in Meadows. Applying
  `deepnorth-sandbox` maxes that factor.
* **ZenWorldSettings** uses `defeated_*` keys to keep high-tier spawns out of
  early biomes. Adding keys makes those spawns eligible; a `pre-yagluth` party
  standing in the Black Forest will meet things the Black Forest would not
  otherwise produce.
* **ZenRaids** appends global keys to player keys, so raid eligibility moves with
  the chain.
* **EpicLoot** gates loot per biome by key (`EpicLoot/baseconfig/biomedata.json`:
  Meadows/eikthyr, BlackForest+Ocean/gdking, Swamp/bonemass, Mountain/dragon,
  Plains/goblinking, Mistlands/queen, AshLands/fader, DeepNorth/frozenking_p3).
  The map is per-key and order-independent. Critically, `Defer Chest Loot Roll =
  true`, so **every generated chest that nobody has opened yet will roll at the
  new key state** on first interaction. Adding keys retroactively upgrades the
  loot in untouched dungeons across the whole map.
* Structure damage is already off (`Azumatt.AzuWearNTearPatches.cfg`: weather
  damage 0, structural integrity disabled, no damage to player buildings), so
  stations and bases built for a preset will not decay.

The one-at-a-time application with read-back exists precisely because these four
mods all observe the key set: if the chain half-applies, the operator needs to
know exactly where it stopped.

---

## How prefab names were verified

Inventing prefab names is worse than shipping no preset, so every name in
`presets/` is checked against `data/prefab_index.json`, built by
`extract_prefab_names.py` from the Ulfsland install. Five independent evidence
channels:

| Channel | Source | Strength |
|---|---|---|
| `objectdb` | `ItemStacksRewrite` config pair (`<Prefab>_weight` / `<Prefab>_max_stack`). The mod binds one entry per `ItemDrop` in `ObjectDB` at runtime, so this is a real ObjectDB enumeration **including mod items** — 1624 names. | strong, but the deployed file was authored 2026-07-25, i.e. **pre-1.0**, so it has no Deep North items |
| `epicloot` | every quoted identifier in `EpicLoot/baseconfig/*.json` — 1638 names. `iteminfo.json` in particular enumerates real gear prefabs per boss tier and is 1.0-aware (it knows `defeated_frozenking_p3`, `SwordGold`, `ArmorDeepNorthHeavyChest`). | strong and current |
| `recipe` | `Recipe_<Prefab>` assets recovered from the game's own Unity bundles — 481 names. | strong; proves craftability |
| `localization` | `item_<prefab>` keys from the localization CSV in `valheim_server_Data/resources.assets`, matched case- and underscore-insensitively. | strong; only real items ship display strings |
| `bundle` | bare identifier tokens from the shipped asset bundles. | weak on its own; the **only** channel that can see build pieces |

`extract_prefab_names.py` reads the UnityFS containers directly (LZ4/LZMA block
decompression, stopping at the end of the first serialized node) rather than
depending on an asset-ripper install. Full scan of all 796 bundles takes ~10s.

Items are `verified` when at least one of the four strong channels has them;
build pieces are verified by the bundle token, corroborated where a
`piece_<name>_description` localization token also exists.

**Result: 337 distinct prefab references across the 9 presets — 294 items and 43
build pieces — all verified, 0 weak, 0 unknown.**

```
$ tools/jumpstart/jumpstart.py verify
9 preset(s), 719 prefab references, 337 distinct
items=294 pieces=43
verified=337 weak=0 unknown=0
```

Five pieces (`charcoal_kiln`, `fire_pit`, `piece_chest_wood`,
`piece_chest_blackmetal`, `portal_wood`) rest on the prefab token alone with no
description key to corroborate; `verify` says so explicitly.

The channels discriminate rather than rubber-stamp. Plausible-looking invented
names come back `unknown`: `SwordSteel`, `MeadFrostResistance`, `FeatherCloak`,
`EmberCharge`, `piece_chopping_block`, `piece_adze`, `piece_workbench_ext5`,
`blackforge_ext9`. Four real names were found *only* because of this process:

* "Embers" is `FaderEmber`, not `Ember`.
* "Ember Charge" is `BombDynamite`.
* "Seal Pelt" is `SealHide`.
* "Fader's Relic" is `FaderDrop` (and Kall's drop is `FrozenKingDrop`).

Deep North items (`GoldOre` = Petrified Tissue, `Gold` = Bloodgold, `FrostCore`,
`SealHide`, `NornThread`, `FrozenFuel`, `BloodGoldKey`, `Lantern_DN`,
`HatefulBlood`, `BarkaBranch`, the `*Gold` weapon family, the
`ArmorDeepNorth*` / `HelmetDN*` / `CapeDeepNorth*` armour) are verified from
EpicLoot data, recipe assets, localization and bundles — but **not** from a live
1.0.12 ObjectDB dump, because the deployed `ItemStacksRewrite` snapshot predates
1.0. Regenerate the index after the next `ItemStacksRewrite` config rewrite to
close that gap:

```
tools/jumpstart/extract_prefab_names.py --out tools/jumpstart/data/prefab_index.json
```

`data/prefab_index.json` ships the four strong channels in full plus a pruned
bundle-token subset (prefab-shaped names only, ~2k of 620k) to keep the
committed artifact around 160 kB instead of 4 MB. `--keep-bundle-tokens` writes
the unpruned set for ad-hoc archaeology.

---

## Worked examples

Mid-tier, dry run:

```
$ tools/jumpstart/jumpstart.py apply pre-bonemass \
    --players 76561197987967077,76561198781619645 --dry-run

== jumpstart pre-bonemass [DRY RUN] world=Ulfsland
   Full iron kit, root armour and poison mead, ready for the Sunken Crypt boss.
   players: 76561197987967077, 76561198781619645
   ledger:  ~/.local/state/valheim-jumpstart/Ulfsland.json

-- AUTOMATED (90 rcon commands)
   addGlobalKey defeated_eikthyr
       # then re-read globalKeys and confirm the key is present before continuing
   addGlobalKey defeated_gdking
       # then re-read globalKeys and confirm the key is present before continuing
   give 76561197987967077 SwordIron -count 1 -quality 3
   ... 88 more
-- LAUNCH
   SERVER_ARGS='-preset Normal -modifier Combat Default -modifier DeathPenalty Default -modifier Resources More -modifier Raids Default -modifier Portals Default'
-- MANUAL
   skills: NOT APPLIED (no working central skill backend on 1.0.12)
   stations: piece_workbench level 5/5, forge level 2/7, ...
```

Re-running after a partial apply is a delta, not a duplicate:

```
   give 76561197987967077 Iron -count 60 -quality 1      # ledger had 40/100
-- SKIPPED (2, already satisfied or out of scope)
   give 76561197987967077 SwordIron q3: ledger already has 1/1
   give 76561197987967077 ArrowIron q1: ledger already has 150/150
```

Committing for real (only after a sibling has proven RCON against the live
server):

```
tools/jumpstart/jumpstart.py apply pre-bonemass --players 7656... --commit
```
