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

### General north transport

The pipeline drives `roads/north.py network` for both route passes. Stage 4
derives budgeted union tiles covering every eligible ordered coarse frontier
corridor. Settlement and north requests sample the real integer Heightmap
lattice with preserved boundaries; the scanner explicitly uses the rendered
Heightmap blend. Existing half-grid bytes require a real rescan, not relabelling.
Survey windows identify where a measurement came from, not a road landmass.
Settlement road verdicts evaluate a covering rooted window without stitching
independent graphs together. Publications retain the measurement `patch` and
name the road's `topology_window`; declared harbour nodes separately publish
their `routing_window`. Routing consumers use those frames and preserve
`measurement_patch` for provenance. Regenerate stage 8 when a publication lacks
this routing evidence; an old survey ID must not silently become an island.

Town discovery grows bounded native views around physically joined trunk roots
until their measured land components are enclosed. A covering window does not
make an unrelated island eligible. Published `road.routing_view` records the
actual source measurement and native sample bounds. Road planning chooses one
genuine measurement covering both endpoints and their approved topology views;
it never stitches independent surveys or reopens a whole-world corridor merely
because that measurement supplied a local town. Joint north/water rereads use
the same recorded view. Stage 8 explicitly depends on geography.

World facts and installation placements now precede geography (stages 5, 6,
then 7). Crossing costs read this world's actual solved portal hub. Prior
geography output and an additional human-defined anchor are not inputs, and
the resulting geography is world-scoped.

Crossing eligibility runs the canonical routed bank approaches, profile fit,
terrain rasterization and native deck preparation/guards. A straight-axis
failure is not proof that a curved approach is impossible. Physical terrain
inputs require a content-bound `.source.json` receipt from `roads.field.request`
and actual integer 1 m Heightmap samples; old receipts are never handwritten
onto unverified bytes.

Trunk identity comes from complete rendered topology, not coarse global area
rank. The primary root is the actual spawn; secondary candidates require
substantial progression-allowed land and a measured coast gap within the
canonical bridge-span bound. Selected trunks and eligible alternatives receive
complete measured bounds plus the physical margin. Receipts name exact
`trunk_nomination` roots and component identities; nomination never proves a
crossing joined. Geography refuses old fields without this identity contract.
`roads/field.py request --bootstrap-field <field> --request-only` validates an
existing genuine receipt and content before printing a coordinate-free request.
Reuse never rewrites the bootstrap, and final issuance still performs a real
scan matching the bootstrap's complete-world height and biome bytes.

Approach search shares the canonical directed edge-cost calculation and isolated
water-feedback masks. Each distinct bank road is fitted and compiled once;
paired terrain and deck guards still evaluate the actual combined surface.
Only direct nonterminal seating refusals can be reused, with identical span,
datum, resolved station-spacing policy and every consumed terrain value.
Exhausted optional measurement coverage is `unverified_coverage`, not physical
impossibility. Required span coverage still refuses immediately, and unmet
transport remains explicit.

The physical sampler indexes measurement extents by native Heightmap zone.
Footprint probes inspect only local windows, including every overlap; the index
does not cache heights, merge disagreeing measurements, or fill unread corners.
`roads/geography.py selftest` includes deterministic query-work and sampling
boundary cases alongside the physical construction/coverage guards.
Paired crossing search stops as soon as either bank is completely exhausted
without a legal approach; it never caps the remaining viable handoffs. Geography
prints ranked pair starts/results with elapsed time and emits bank handoff
counts at 30-second intervals between goals. These are search-work indicators,
not a completion percentage or a promise that later construction will pass.

Crossing queries default to two bounded fork workers (`geography.py emit
--workers 1` selects serial execution). Results and exceptions are consumed in
rank order, not completion order; accepting a candidate cancels and reaps unused
workers. Native terrain and graph snapshots are inherited, not copied per query.
The pipeline records child-only producer inputs, including controlled cancellation.

An exhausted base routing tree proves all destinations outside its reachable
set unavailable at once. Only a wholly empty eligible routing frontier can be
reused across bank queries, keyed by current corridor arrays, masks, geometry
and policy. Wet-footprint and compiler failures are not empty-bank certificates.
Execution/cache counters are excluded from deterministic geometric evidence.

Live builders measure a heavy world manifest only when creating a ledger.
Compatible named postcondition counts share bounded groups of at most eight
distinct commands and one log interval; RCON requests remain synchronous.
Every response must have its own exact echo and complete count table. Missing,
ambiguous or truncated responses remain unknown, never zero; wildcard, duplicate
and unsupported queries retain individual observations.

The default seed uses the same derived request path as every other seed; no
historical scan-box geometry supplies a default. Stage 4 first measures the
complete playable domain at 1 m to identify rendered land components, then
freshly measures the final population and checks that the complete-world height
digest is unchanged. Ferry separation uses those complete component identities,
never labels recomputed inside cropped survey windows.

`roads/shores.py` solves the boathouse, jetty and both eastern ferry terminals
from current measured terrain and the world's solved installations. Serving
roads use the full measured approach window. The far terminal requires its own
`F1-ferry-eastisle-inland` road; it is transport-dependent, not connected by an
imaginary walking bridge. Construction and retained waterfront completion share
canonical native-pose reconciliation. Saved acceptance independently requires
each terminal's final built serving road and physically accepted voyages.

Stage 9 attaches a provisional itinerary to an actual station of the generated rooted
network; no standing N0b junction, old north node table, ledger replay, or fixed
ferry coordinates generate it. Census covers the roads, terminal search boxes,
and sea lanes before the final pass. Stage 11 uses the existing route/profile,
writer-water, location and census gates and merges north road-ground into the
canonical seed-scoped segments/crossings files. Historical `north_segments.yaml`
is diagnostic evidence, not a second generated network.

Source junctions are judged against the actual levelled road footprint, including
its inclusive end caps; a harmless lattice snap is not a disconnected road.
Bridge flags alone cannot establish walking coverage. A deck source requires
the canonical bank handoff and a surviving floor from the actual deck builder,
including structural and carriageway guards on the composed planned surface.
Source checks run after joint fitting and use the same collapsed crossing
population exported for construction. This is planning evidence, not a saved
world or a claim that a deck has been built.

North solo routes use the canonical adjacency and drowned-terminal remedies;
later water rebuilds preserve successful trim, land-only and span-price choices.
Refusal diagnostics identify the binding gate and its measurements.

Later ground writes use complete, emission-bound road models to protect existing
road approaches and ownership bands. A newer design or a filtered historical
profile does not grant permission to move another road's ground. Rim closure
may cross natural ground only while every existing physical/ownership guard
still passes.

Deck ground prerequisites accept either an attributed terrain-moving record or
a completed bridge-only ground unit bound to this build's command and scope.
The latter is not a terrain write and does not waive any deck guard. Landings
are measured after assembly and trimming, using native floor bounds and actual
station ownership; compatible existing floors contribute walking coverage
without duplicate emissions. Planning and construction share `prepare_geometry`.

Provisional base-road joint/writer refusals remain explicit `base_route_refusals`
and keep their full survey/census geometry; they are not blamed on every north
candidate or silently labelled clear. North-road failures still reject that
candidate. A provisional choice is census guidance, not a highest-buildable or
construction claim. The census-backed pass tries the complete ordered frontier
again and applies all combined base/north gates. Final loaders, transport
writers and independent saved-world acceptance reject either outstanding base
or north road debt, even if an artifact claims `provisional: false`.

`VH_NORTH_PRESET` selects a shipped preset (default `pre-kall`), explicitly passed
as `field.py request --north-preset` and `north.py network --preset`. Permitted
biomes come from the preset tier chain, not distance tables. Pre-Kall requires
the preset's `defeated_fader` key and stops road construction before Deep North.
The north frontier report distinguishes its coarse search resolution from the
fine measured road endpoint; it is not a claim of an exhaustive 1 m whole-world
maximum. Unread or incompatible fine coverage is a refusal requiring stage 4,
not permission to invent heights or truncate the route.
Candidates are tried in descending northing through fine road, dock, full-hull
voyage and lighthouse feasibility, without an arbitrary top-N limit. A failed
coarse winner does not end the search. The accepted itinerary retains the
complete rejected-higher-candidate prefix and its source, raw-field and census
identities. Acceptance independently reconstructs that order and reruns those
rejections, as well as measuring the selected construction in the frozen save.
A short route or serialized refusal flag cannot redefine the required frontier.

The itinerary (`worldseed.north_path`, override `VH_NORTH_ITINERARY`) declares
`schema_version: 1`, seed/world, explicit `provisional`, progression `phase`,
generated `nodes`, required `road_edges`, `terminals`, paired `transport` with
`sea_route`, the unique `final_departure`, attachment evidence, and the measured
field citation. Each terminal's `segment_id` serves its exact `node_id`/`xz`.
Every non-departure terminal belongs to a real crossing pair. The final departure
can stand alone when the next journey is into Deep North. Missing required roads,
boats or lighthouse never become an empty successful job.

The standing-world R3/R4 node/edge tables and their old `harbour-vestvik` landing
are no longer regeneration inputs. Their two semantic requirements remain in
`required_connections`: solved Vestvik must reach the actual base/north network
(generated W8 or `declared_sites.site_legs`), and must reach a real solved harbour
(the nearest eligible declared harbour landing via `declared_sites.legs`, or the
solved early-dock/sandbox-harbour via T5/T9). These are journey requirements, not
a demand to reproduce the old repair geometry. Both endpoint names must exist
on actual built roads and be root-reachable in the independently measured graph;
an unbuilt-parent false junction or missing harbour is a refusal, not omission.
When the site solver withholds Vestvik, its measured reason accompanies the
still-required connection. No 45.6087 datum or standing N0b coordinate survives
this cutover. Base and north profiles also share the original router's joint/
writer-water solve through its optional in-memory `plan_state` output.

Offline regression commands (not proof of a constructed world):

```sh
python3 roads/north_itinerary.py selftest
python3 roads/north_transport.py selftest
python3 pipeline.py selftest
python3 buildstage.py selftest
env -u VH_SEED -u VH_WORLD python3 -m accept --selftest --section roads,north_transport,emission,provenance
```

Acceptance fixtures construct several different seed scopes; do not force a
single `VH_SEED`/`VH_WORLD` into that selftest process. Actual builds and saved
world acceptance still require their explicit seed/world scope.

The integration owner must regenerate the expanded field, run the ordinary
provisional/census/final fixed point, build through Buildstage, then run full
saved-world acceptance in the isolated world. Repeat the same gate for another
seed. These implementation notes do not assert that either runtime proof ran.

`roads/north_transport.py plan|build|measure|selftest` consumes the seed's
`worldseed.north_path(seed)` itinerary (`--itinerary` overrides it). Supply
`--field PATH` or `VH_NORTH_FIELD`: a seed-matching, integer-lattice 1 m
PatchScan covering the terminal, hull and lighthouse footprints. Combined
fields may retain unrelated unaligned survey patches: transport ignores and
reports them, never rounds or resamples them, and refuses any requested point
without aligned coverage. No historical harbour coordinates or fixture fields
are borrowed. For example:

```sh
python3 roads/north_transport.py plan --seed SEED --world WORLD --field FIELD
python3 roads/north_transport.py build --seed SEED --world WORLD --field FIELD --terminal north-dock-000 --actor ACTOR --apply --out REPORT.json
python3 roads/north_transport.py measure --seed SEED --world WORLD --field FIELD --actor ACTOR --out PHYSICAL.json
python3 roads/north_transport.py selftest
```

These are operator commands, not a claim that this checkout has built anything.
Buildstage step 6.75 creates one road-dependent unit per terminal, after all
ground and crossing work and before roadside/recheck. Missing, foreign,
provisional or incomplete itineraries refuse. The serving road must already
have normal ledger ground writes and its endpoint must exactly equal the dock
root; transport never creates terrain or claims protected ground. Both ends
are measured before either writes. A dock whose applied root is more than
0.35 m from its deck, an unfloatable full hull, or an unresolved lighthouse
seat refuses rather than omitting the boat or bypassing its ground owner.
Every declared pair must carry `sea_route`. Planning checks connected water
and the terrain vertices under the continuously swept full hull, including
rotation between heading samples. A shallow vertex, disconnected basin or
field gap refuses with its measured location; two floatable berths do not
establish a crossing. Saved-world acceptance independently checks the swept
terrain and continuous hull–solid intersections, not just integer points or
isolated poses. Dock foundations are measured against saved ground too.

The hull envelope is the conservative convex projection of all measured solid
colliders in the prefab-root frame, preserving off-centre bows and corners.
An ellipse fitted inside the hull dimensions is not a full-hull clearance
proof. Static berth samples and continuous sweeps share the measured envelope;
missing geometry refuses rather than reducing it to the ship's pivot.
Saved-solid heights bound overhead collision checks, and the observed keel can
increase required depth. A water-relative air-draft estimate cannot hide a mast
or certify a ship whose actual bottom penetrates the bed.

The final terminal requires the verified `drake-lighthouse.blueprint` library
body through the ordinary blueprint converter, at least 15 m tall, plus a
berth-verified large OdinShip vessel, with no vanilla longship substitute.
It is not a lamp or beacon prop. Spawn plans and ships use
the normal BuildLedger/LiveBuilder path and save postconditions. Live readback
is bounded per physical location; final saved-world acceptance independently
checks the actual pieces, road-to-dock walk, hulls and lighthouse. Run that
acceptance after building; intended plan records alone are not completion.

Resuming a partially built terminal reconciles source poses one-to-one against
live position/rotation evidence before sending anything. Only missing pieces
are emitted; duplicates, extra pieces or truncated census responses refuse
without demolition. Execution records describe the sent subset and separately
retain the immutable full source geometry and blob identity.

Modded geometry and physics measurements must use the effective content
configuration as well as the plugin set. Set `VH_CONFIG` to its host directory
when `$VH_SRC/BepInEx/config` points inside a container:

```sh
MODS=ALL OUT=/tmp/piece_geometry.tsv SANDBOX=/tmp/piecegeom/vh \
  bash blueprints/run_piecegeometry.sh
MODS=ALL OUT=/tmp/piece_material.tsv SANDBOX=/tmp/piecemat/vh \
  bash crossings/run_piecematerial.sh
```

Both commands require `VH_SRC`; export `VH_CONFIG` too when the default is not
readable. They refuse missing modded configuration before staging, materialize
configuration links into private files, and record the source in `.mods`.
Operational `org.tristan.rcon.cfg` is deliberately excluded so the diagnostic
does not reuse the production listener's credentials. This exception does not
change the measured content configuration. Dump success is not saved-world
acceptance; rebuild the corpus with the actual game-version label before use.


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

Stack sizes come from `max_stack` in the same measured item facts. The absent
ItemStacksRewrite mod's historical profile config is not a runtime dependency.
If that mod is installed, set `inventory.stack_table` to its active config:
absolute paths stay absolute; relative paths use the call-time
`VALHEIM_WORLD_ROOT` / `VALHEIM_ROOT` selection. A declared unreadable override
refuses. Unmeasured items retain explicitly labelled fallback/unknown pricing;
neither the 32-slot grid nor the carry-weight limit is increased.

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
