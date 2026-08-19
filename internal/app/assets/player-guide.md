# Hrafnheim player guide

Hrafnheim is the Neuralyze Valheim world. Game build **0.221.12**. The world runs a large mod set
(105 server plugins, 128 client plugin entries) and this guide is organised by what you want to do,
not by which mod does it. Each mod name is given only so you can find the setting in its own config
file if you want to change something on your side.

Assumes you have played vanilla Valheim.

## How to read the keys in this guide

Every key printed here was read from a configuration file on this server or client, or from the
mod's own documentation where no file exists. Three provenances are used, and they matter:

| Marker | Meaning |
| --- | --- |
| **shipped** | The release ships this value in the profile. It overwrites your local copy on every sync. You cannot keep a different value. |
| **default** | The mod generated its own config on first run and the release has no opinion. This is the mod's default. You may change it and your change survives syncs. |
| **documented default** | No config file is present at all. The mod runs on its code defaults, taken from its own documentation. |

Anything marked `[Synced with Server]` inside a config is decided by the server and your local edit
is ignored. Hotkeys are almost always `[Not Synced with Server]`, which is why the release ships the
ones that matter rather than syncing them.

Several keys are claimed by more than one mod. Pressing such a key fires **every** mod bound to it.
See [Key conflicts](#key-conflicts).

---

## 1. Joining and updating

You do not launch Hrafnheim from Steam and there is no Steam launch-option step.

1. Sign in at `https://valheim.neuralyze.com` and open the Hrafnheim world page.
2. Download **Valheim Profile Sync** for Windows and run it once.
3. Pick the card that matches how you play (flat or VR) and choose **Install or update**.
4. The app verifies your access and the release checksums, downloads only files that are missing or
   changed, keeps this profile separate from your normal Valheim install, and then launches your
   existing Steam copy of Valheim with the profile applied.
5. It also puts a Desktop shortcut on your machine for that profile. **Use that shortcut every
   time.** It checks for updates first and then launches.
6. Valheim opens at its own main menu, not in the world. Choose **Start game**, pick or create a
   character, then **Join Game**.
   - The profile ships Azumatt FastLink with Hrafnheim already in its server list, so you can
     connect from the FastLink panel without typing anything. The password prompt is switched off
     because the address and password are already in the shipped server list.
   - The manual path still works: the **Join IP** tab, address `valheim.neuralyze.com`, port `2457`.

### What a sync actually does, and what it destroys

The sync builds a complete new profile tree and then swaps it in wholesale. The old tree is renamed
to `previous/` and the new one becomes `active/`.

- **Any file the release ships is replaced.** Your edits to it are gone. That includes the ward
  config, the magic config, the hip-lantern config, the AdminQoL config, the PerfectPlacement
  config, the AzuAutoStore config, the crafting-UI config, FastLink, and the VR fixes config.
- **Config files the release has no opinion about are carried across by name.** If a mod generated
  its own config and the release does not ship one, your edits to it survive.
- **Everything outside `BepInEx/config` is replaced unconditionally.** Do not put anything you care
  about inside `active/`.
- Your own revealed map and pins are deliberately stored *beside* `active/`, not inside it, so a
  sync cannot delete them.

<!-- guide:flat -->

If a sync fails with a message about setting the active profile aside, close Valheim and run it
again.

<!-- /guide:flat -->

<!-- guide:vr -->

If a sync fails with a message about setting the active profile aside, close Valheim **and SteamVR**
and run it again.

<!-- /guide:vr -->

---

## 2. Vanilla basics

These are the game's own bindings. They are rebindable in **Settings → Controls** and are not read
from any file on this server, so treat them as the stock defaults for 0.221.12 rather than as
server facts.

| Action | Default |
| --- | --- |
| Move | `W` `A` `S` `D` |
| Sprint | hold `Left Shift` |
| Jump | `Space` |
| Crouch / sneak | `Left Control` |
| Dodge | `Left Alt` while moving |
| Attack | `Mouse0` |
| Block / secondary | `Mouse1` |
| Use / interact | `E` |
| Alternate interact | `Left Shift` + `E` |
| Hotbar slots | `1`–`8` |
| Inventory | `Tab` |
| Map | `M` |
| Map ping | `Mouse2` on the map |
| Skills | `F2` |
| Build / hammer menu | equip the hammer, `Mouse1` to open, scroll or `Q`/`E` to change tab |
| Rotate a piece | scroll wheel |
| Repair | hammer equipped, `Mouse1` to enter repair, click a piece |
| Chat | `Return` |
| Sit | `X` |
| Hide HUD | `Ctrl` + `F3` |

Everything below either adds to this or overrides it. Where a mod takes a vanilla key for something
else, it is listed in the conflicts block.

<!-- guide:vr -->

**How to read every key table in this guide from a headset.** VHVR does not take your keyboard away,
but you cannot see it, and most of the mod bindings printed here are held modifiers, chords or scroll
gestures that are impractical blind. Treat the key tables as the flat player's route. Yours is the
wrist radial menu and the contextual hover menu that the profile's own `NeuralyzeVRFixes` plugin
adds; both are listed in full in [VR players](#10-vr-players), together with the honest list of
controls that have no VR route at all. Where a section has a VR-specific route, it is called out in
place. The vanilla actions in the table above are performed with the motion controllers rather than
with these keys: blocking is gestural, bows are drawn two-handed, and there is no block key.

<!-- /guide:vr -->

Two vanilla behaviours change on this server regardless of any key:

- **Unlock popups are suppressed.** New recipe, item, piece, station, material and trophy popups no
  longer interrupt you; the text still appears in the message log. (AdminQoL, shipped)
- **Skill level-up effects and the level-up message are removed**, and equipping is instant instead
  of using the timed equip animation. (AdminQoL, shipped)

---

## 3. Inventory and storage

There is **no item-drawer mod** on Hrafnheim. Storage is chests. Chests are bigger than vanilla
(BiggerChests) and more than one player can have the same chest open at once without desyncing
(MultiUserChest).

### Moving items in bulk

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `P` | Quick stack everything that already has a stack in a nearby container (10 m). With a container open it stacks only into that container. | Quick Stack Store Sort Trash Restock (default) |
| `L` | Restock ammo and consumables from nearby containers (10 m). With a container open it restocks only from that one. | Quick Stack Store Sort Trash Restock (default) |
| `O` | Sort the open container, or your inventory | Quick Stack Store Sort Trash Restock (default) |
| `Delete` | Trash the item under the cursor | Quick Stack Store Sort Trash Restock (default) |
| `Left Alt` or `Right Alt` (hold) | Left-click an item, or right-click a slot, to favourite it. Favourited items and slots are excluded from quick stack, store, sort and trash. | Quick Stack Store Sort Trash Restock (default) |

<!-- /guide:flat -->

<!-- guide:vr -->

None of these keys are on the wrist menu: `P`, `L` and `O` are each claimed by more than one mod, and
a wrist button is a one-frame pulse that would fire all of them. Point the laser at a container and
hold the **off-hand grip** instead — the container hover menu carries Quick Stack, Restock, Sort and
Store All. The `Delete` trash key and the `Alt`-hold favouriting modifier have **no VR route**; a
held modifier cannot be pulsed from a wrist button.

<!-- /guide:vr -->

Restocking only touches ammo and consumables. Quick stack includes your hotbar. This mod also draws
its own buttons next to the container window, so you never strictly need the keys.

### Auto-store

Nearby chests pull items out of your inventory on their own, on a 15 second interval after a 10
second settling delay, within 10 m (falling back to 20 m). A chest only takes an item type it
**already holds**, so it will not invent new storage decisions for you. Chests also pick items up
off the ground near them. Your hotbar is never auto-stored.

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Period` | Store your whole inventory into nearby containers now | AzuAutoStore (shipped) |
| `Left Shift` + `Period` | Pause automatic storing | AzuAutoStore (shipped) |
| `Middle mouse` | Store just the item you click | AzuAutoStore (shipped) |
| `Y` (hold) + click an item | Search nearby chests for that item | AzuAutoStore (shipped) |
| `Z` (hold) | Left-click an item or right-click a slot to favourite it against auto-store | AzuAutoStore (shipped) |

Auto-store favourites are a **separate list** from the quick-stack favourites above, with a
different modifier key. If an item keeps disappearing into a chest, `Z`-favourite it; if it keeps
getting swept by `P`, `Alt`-favourite it.

<!-- /guide:flat -->

<!-- guide:vr -->

Auto-store needs no input, so it behaves in VR exactly as described above: a chest takes only what it
already holds, and your hotbar is never touched. **Store All** on the container hover menu is the
on-demand version. `Period`, the `Left Shift` + `Period` pause and both favouriting holds have **no
VR route**, so from a headset you cannot exempt a single item from auto-store. Anything you do not
want swept into a chest belongs in your hotbar, which is never auto-stored.

<!-- /guide:vr -->

### Crafting from containers

You do not need materials in your inventory. Crafting, building, repairing and station fuelling pull
from any container within **20 m** (AzuCraftyBoxes).

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Left Shift` (hold) | Pull all available fuel or ore in one go | AzuCraftyBoxes (default) |
| `Left Alt` + `O` | Turn all container pulling off, as if the mod were not installed | AzuCraftyBoxes (default) |

<!-- /guide:flat -->

<!-- guide:vr -->

Container pulling is automatic and needs no key, so crafting, building, repairing and station
fuelling from nearby chests work unchanged in VR. The fill-all modifier is a hold and the master off
switch is a chord, so neither has a VR route — which only means you cannot turn the convenience off
from a headset.

<!-- /guide:vr -->

### The crafting window itself

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `F` | Toggle the favourite-recipe filter | AAA_Crafting (shipped) |
| `Left Control` (hold) | Show the minus button instead of plus on a recipe icon | AAA_Crafting (shipped) |
| `Left Control` + scroll over the amount box | Jump straight to the maximum you can afford | AAA_Crafting (shipped) |
| `Left Shift` + scroll over the amount box | Step by 10 | AAA_Crafting (shipped) |
| `Left Shift` + `PageUp` | Toggle the alternate recipe UI | AAA_Crafting (shipped) |

<!-- /guide:flat -->

<!-- guide:vr -->

Every shortcut in the crafting window is a modifier. The `Left Control` and `Left Shift` scroll
gestures cannot be pulsed from a wrist button, and neither the favourite-recipe filter nor the
alternate recipe UI is on the wrist menu. The alternate-UI chord is additionally untested against
VHVR's `PageUp` head-reposition binding — see [Unverified](#unverified). Craft from the window as it
comes.

<!-- /guide:vr -->

### Extra slots

Your inventory gains an equipment row shown in its own panel, plus a **Wishbone slot**, a
**Demister slot**, and **3 quick slots** (AzuExtendedPlayerInventory). Items auto-equip to the right
slot. There are also Vanity and Loadout buttons in the inventory for cosmetic overrides and saved
equipment sets. No extra inventory rows are granted.

| Key | Action | Mod |
| --- | --- | --- |
| `Left Alt` + `Z` | Use quick slot 1 | AzuExtendedPlayerInventory (default) |
| `Left Alt` + `X` | Use quick slot 2 | AzuExtendedPlayerInventory (default) |
| `Left Alt` + `C` | Use quick slot 3 | AzuExtendedPlayerInventory (default) |

The on-screen quick-slot strip is **hidden** by the shipped config. This is deliberate: drawing it
caused a HUD flicker in VR. The keys still work. Bindings for quick slots 4–8 exist in the config
(`Alt` + `V`/`B`/`N`/`1`/`2`) but only three slots are enabled, so they do nothing.

`Left Alt` + `Z` is also RequipMe's manual re-equip key. See the conflicts block.

<!-- guide:vr -->

Whether the quick-slot chords can be triggered at all from a headset is **not established**: they are
chords, and the on-screen strip that would confirm the slot fired is switched off. Treat the three
quick slots as unavailable in VR until you have proved otherwise on your own rig, and put anything
you need in a hurry on the hotbar instead.

<!-- /guide:vr -->

### Backpacks

Craftable at a level 2 workbench, with a dedicated back slot. Backpacks open automatically when
equipped and auto-fill. They hold 100 weight, cannot be nested, and cannot be put in chests. The
teleport check applies, so a backpack full of ore will still stop a portal. There is no hotkey.

---

## 4. Building

### Free rotation and fine placement

PerfectPlacement replaces vanilla's snap-only rotation. All of these are **shipped**, so they are the
same for everyone.

| Key | Action |
| --- | --- |
| `Left Alt` (hold) | Rotate the ghost on Y in 1° steps, and align to the build grid |
| `C` | Rotate on X |
| `V` | Rotate on Z |
| `F` | Copy the rotation of the piece in front of you |
| `G` | Set rotation perpendicular to the piece in front of you |
| `F7` | Toggle grid alignment |
| `F6` | Change the default grid alignment |

### Advanced Building Mode

Enter while placing a piece to move and rotate the ghost freely instead of snapping.

| Key | Action |
| --- | --- |
| `F1` | Enter Advanced Building Mode |
| `F3` | Exit |
| `F7` | Reset the piece to its original position and rotation |
| `Keypad3` | Copy the selected object's rotation |
| `Keypad8` | Paste that rotation |
| `KeypadPlus` / `KeypadMinus` | Increase / decrease the move and rotate step (hold `Shift` for steps of 10) |

<!-- guide:vr -->

Nothing in these two tables is on the wrist menu or in a hover group, and the `Left Alt` hold cannot
be pulsed. VHVR's own Advanced Build Mode is switched off in favour of PerfectPlacement, so from a
headset you build on vanilla snapping plus whatever grid alignment is currently set. Do **not** reach
for `F6`: it is claimed by three mods, and pressing it in VR opened an unconverted screen-space canvas
and ended a session — see [Key conflicts](#key-conflicts).

<!-- /guide:vr -->

Advanced **Editing** Mode, which moves pieces that are already built, is admin territory and lives in
the [admin appendix](#11-admin-appendix).

### What the world does to your buildings

Structural integrity is switched off entirely and player-built structures take no damage, including
no weather damage (AzuWearNTearPatches). Boats and carts take no damage and no water damage.
Structures inside a ward additionally take no weather damage (WardIsLove). Practically: build what
you like, at any height, and it will not fall down or rot.

Repairing is quicker than vanilla. A single click repairs continuously (RhythmicRepairs) and area
repair covers a **15 m radius** (AzuAreaRepair).

### Terrain

The hoe and pickaxe get radius and hardness control (AdvancedTerrainModifiers).

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Left Alt` + scroll | Change the terrain-tool radius | AdvancedTerrainModifiers (default) |
| `Left Control` + scroll | Change the terrain-tool hardness | AdvancedTerrainModifiers (default) |

<!-- /guide:flat -->

<!-- guide:vr -->

Radius and hardness are held-modifier scroll gestures and have **no VR route** at all. The hoe and
pickaxe still work from a headset, on whatever radius and hardness are currently set; you cannot
change either while wearing it.

<!-- /guide:vr -->

You can also dig **20 m down** and raise **8 m up**, well past vanilla's limits (DigDeeper).

### Wear and tear, on purpose

RuinsMaker lets you make new builds look old.

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Left Alt` (hold) while hammering | Add wear to the piece you hit | RuinsMaker (default) |
| `Left Alt` + `W` | Add wear to every piece in the configured radius | RuinsMaker (default) |
| `Left Shift` + `W` | Repair every piece in that radius | RuinsMaker (default) |

<!-- /guide:flat -->

<!-- guide:vr -->

Point the laser at a piece and hold the **off-hand grip**: the Piece hover menu carries **Add Wear**
and **Repair Area**. Those two were re-implemented as hover actions precisely because a hover action
can hold a key and interact in the same frame. The plain hammer-modifier version has no VR route, so
wear is applied per-radius rather than per-swing.

<!-- /guide:vr -->

### Moving decorations

<!-- guide:flat -->

ZenRedecorate: hold `Left Control` to enter move mode, then `Mouse0` to pick a piece up and place it
again. Useful for furniture without deconstructing.

<!-- /guide:flat -->

<!-- guide:vr -->

ZenRedecorate needs a held `Left Control` plus a mouse click, and it appears in no hover group, so
moving furniture without deconstructing it is **not reachable in VR**. Either place decorations from a
flat session or deconstruct and rebuild them.

<!-- /guide:vr -->

### Extra build-piece sets

These appear as extra tabs or categories in the hammer menu. Category names are as configured on the
server, so you can find them:

| Set | Where it appears |
| --- | --- |
| Odin's Kingdom | **Wood Building**, **Stone Building**, **Deco**, **Furniture** |
| Odin's Undercroft | **Undercroft** |
| Odin's Food Barrels | **Food Barrels** |
| Rusty Build Pieces | **Rusty Pieces**, plus entries under Crafting |
| Ravenwood Restorations | **Raven Restorations** |
| Core Wood Pieces | **CoreWoodPieces**, **CoreWoodFence** |
| Boat Additions | **BoatAdditions** |
| Crafty Carts | **Crafty Carts** |
| Odin Architect, Odin Campsite, Basements, Odin's Horse Pen, wards | **Misc** |
| Pottery Barn, Sears Catalog | folded into the vanilla tabs; Sears Catalog widens the build panel to 15 columns × 6 rows |
| Comfy Ladders | not a piece set — makes existing pieces climbable like a ladder. Enabled for the Ashlands steep stair, the grausten stone ladder and the wood stepladder; the goblin stepladder is not enabled |
| Blacksmith's Tools, Blacksmithing Expanded, Shipwright | station and tool pieces at the relevant crafting station |
| Item Stand All Items, ZenItemStands | any item can go on any item stand |

Comfort has its own reference list: press `F6` to show or hide the list of comfort pieces
(ComfortTweaks, default). Note `F6` is claimed by three mods.

<!-- guide:vr -->

`F6` is the comfort-list key and also the key that ended a VR session — see
[Key conflicts](#key-conflicts). There is no wrist entry for the comfort list, so treat it as
unavailable in VR.

<!-- /guide:vr -->

---

## 5. Combat, magic and abilities

### EpicLoot

Loot is enchanted with rarities Magic (blue), Rare (yellow), Epic (purple), Legendary (teal) and
Mythic (orange). The enchanting table supports Sacrifice, Convert Materials, Enchant, Augment,
Disenchant and Upgrade. Adventure mode (bounties, treasure maps, gambling) is on, with a cap of 5
bounties per player. Item drops are gated: killing a boss unlocks that biome's items. Boss trophies,
crypt keys and wishbones drop one per player within 100 m of the boss, so nobody has to fight over
them. Enchants do **not** transfer to crafted items, and extracting a rune destroys the item.

Enchanted gear can grant an active ability. Abilities sit on a bar in the lower left.

| Key | Action | Mod |
| --- | --- | --- |
| `G` | Ability slot 1 | EpicLoot (shipped) |
| `H` | Ability slot 2 | EpicLoot (shipped) |
| `J` | Ability slot 3 | EpicLoot (shipped) |

`G` and `H` are both contested. `G` is also PerfectPlacement's perpendicular-rotation key (only
matters while you are holding a hammer) and AdminQoL's prefab inspector (admin only). `H` is also
AdminQoL's copy key. If an ability does not fire, check whether you are in build mode.

<!-- guide:vr -->

Whether the three ability keys can be fired from a headset is **not established**. They are not on
the wrist menu and no hover group covers them. EpicLootVRFix is installed, which suggests EpicLoot's
own UI is handled, but nothing in the config says how an ability is triggered in VR — see
[VR coverage that is not established](#vr-coverage-that-is-not-established).

<!-- /guide:vr -->

### Spells

MagicRevamp adds a spellbook, elemental essences and summons.

| Key | Action | Mod |
| --- | --- | --- |
| `Keypad1` | Cycle to the next spell in your inventory | MagicRevamp (shipped) |
| `Keypad2` | Unsummon your summons | MagicRevamp (shipped) |

`Keypad1` is also PerfectPlacement's Enter Advanced Editing Mode. Both are shipped, and this is a
real collision — see the conflicts block.

<!-- guide:vr -->

Both spell keys are on the wrist radial menu, as **Cycle Spell** and **Unsummon**, so spells are fully
reachable in VR. Cycling a spell still pulses `Keypad1`, so it also enters PerfectPlacement's Advanced
Editing Mode if you are an admin looking at an object.

<!-- /guide:vr -->

### Forsaken Powers

You keep more than one Forsaken Power and cycle between them.

| Key | Action | Mod |
| --- | --- | --- |
| `Keypad4` | Cycle to your next Forsaken Power | ForsakenPowersPlusRemastered (shipped) |
| `F9` | Reset the power cooldown so you can use another | ForsakenPowersPlusRemastered (shipped) |

<!-- guide:vr -->

**Cycle Power** on the wrist radial menu pulses `Keypad4`, so cycling powers is reachable in VR. The
cooldown reset is not on the wrist menu and has no VR route.

<!-- /guide:vr -->

### Bows and blocking

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Mouse1` | Zoom while drawing a bow | BowsBeforeHoes and VHModpackFix (default) |
| `E` | Cancel a bow draw | BowsBeforeHoes and VHModpackFix (default) |
| `Left Shift` (hold) | Quiver bar modifier, for swapping arrow types from equipped quivers | BowsBeforeHoes (default) |

Two mods bind the same two bow keys to the same two actions. That is intentional: VHModpackFix
supplies a VR-safe implementation. It is not a conflict in practice because the effect is identical.

<!-- /guide:flat -->

<!-- guide:vr -->

**Shooting a bow.** The bow sits in your off hand — your left, unless you set `LeftHanded`.
Your other hand does the work, and it takes four steps:

1. **Fetch an arrow.** Reach that hand behind your shoulder, or down to your waist, and press
   **grab**. An arrow appears on the string. The same reach and grab puts it away again.
2. **Touch the string.** Bring the hand to within about 20 cm of the arrow rest. Farther away and
   nothing happens when you press anything.
3. **Hold trigger or grip** to pinch the string, and pull your hand back. About 60 cm is a full
   draw; the distance is `BowFullDrawLength` and can be shortened.
4. **Let go** to shoot. The shot happens on release, not on press.

**If nothing fires, you have no arrow nocked.** Step 1 is the one everybody skips: without it the
string will not move and releasing does nothing at all. And a trigger tap with no draw fires a dead
arrow into the ground on purpose — full draw restriction makes early releases inaccurate.

Three settings make this easier. `BowFullDrawLength` lowered to around 0.35 suits a small play space
or a headset that loses your hands near your body. `BowDrawRestrictType = Partial` gives full power
from the start of the pull. `OneHandedBow = true` removes the pulling motion entirely: hold the
trigger, release to shoot, always full power — the bow stays in your off hand either way.

Bows are otherwise physical and two-handed, with accuracy that ignores draw length and an
arrow-prediction graphic. Crossbows reload manually. There is no zoom key and no cancel key — you
simply lower the bow. **Blocking is gestural**: you raise the shield yourself and there is no block
key at all. The quiver-bar modifier is a hold and has no VR route, so change arrow types from your
inventory. VHModpackFix exists to supply a VR-safe bow path, which is why it duplicates the flat
bindings rather than conflicting with them.

<!-- /guide:vr -->

BowsBeforeHoes also adds tiered bows and quivers, each unlocked at the trader by a boss kill
(Black Forest bow and quiver after Eikthyr; Surtling bow and OdinPlus quiver after the Elder; Lox
quiver after Bonemass; Seeker bow, quiver and arrows plus mist torch arrows after the Goblin King).
Most bows and quivers carry a small movement-speed penalty.

Blocking, parry stamina and block power scale with skill (ImpactfulSkills): block power improves from
level 25 and blocking starts returning stamina from level 40.

### Harpoons

| Key | Action | Mod |
| --- | --- | --- |
| `T` | Pull the harpooned target closer | HarpoonExtended (default) |
| `Left Shift` (hold, while the harpoon is in flight) | Always pull yourself to the target instead | HarpoonExtended (default) |
| `Left Control` + `T` | Release the line | HarpoonExtended (default) |
| `Left Shift` + `Left Control` + `T` | Stop harpooning entirely | HarpoonExtended (default) |

<!-- guide:vr -->

The pull key is not on the wrist menu and the two release chords cannot be pulsed, so the harpoon line
has **no VR route** beyond the throw itself. A harpoon you cannot release is a harpoon you should
think twice about throwing.

<!-- /guide:vr -->

### Death and danger

- Dying costs **5% of your skills** and resets progress within the current level, with a 10 minute
  mercy effect and a 50 second safety effect afterwards (DeathPenalty).
- **Cheat Death**: dropping to 5% health (or 10 hp) triggers a 10 second protection window and
  cleanses damage-over-time, on a 10 minute world-time cooldown. There is also a 5% chance the proc
  is free and does not consume the cooldown.
- Creature levels and loot scale with the area and party (CreatureLevelAndLootControl). Extra
  monster modifiers, extra raids (ZenRaids) and extra world locations are active.
- Loot drops instantly with no pop delay (TrueInstantLootDrop). Ground loot is cleaned up over time
  (DropCleaner).
- You can fight while swimming: equipment stays wielded in water (WieldEquipmentWhileSwimming, hammer
  / bronze axe / hoe / antler pickaxe whitelisted for use) and swimming is a real skill.

Armour and weapon sets are added by ValheimArmory, SouthsilArmor, Judes Equipment, DragoonCapes and
Mask. All are crafted at ordinary stations; nothing needs a key.

---

## 6. Skills and gathering

### Which skills exist here

| Skill | Raises | Mod |
| --- | --- | --- |
| Mining | Damage to ore and yield per deposit; explosive mining unlocks at level 50 | Smoothbrain Mining |
| Lumberjacking | Damage to trees, item yield from trees, reduced tree-fall damage, movement speed in forests | Smoothbrain Lumberjacking |
| Farming | Crop growth speed, crop yield, planting and harvesting reach, reduced stamina per plant | Smoothbrain Farming |
| Foraging | Yield from pickables, pickable respawn speed, mass-picking radius, and a respawn timer display from level 30 | Smoothbrain Foraging |
| Endurance | Maximum stamina (up to +75) and stamina regeneration | blacks7ar Endurance |
| Wisdom | Maximum eitr (up to +100) and eitr regeneration | blacks7ar Wisdom |
| Herbalist | Healing amount and duration of potions you brew; bottles are returned | blacks7ar Herbalist |
| Blacksmithing | Weapon and armour durability (up to a 2000 cap) and, from level 100, elemental damage bonuses | Blacksmithing Expanded |
| Carry Weight | Maximum carry weight | CarryWeightSkill |
| Spearfishing | Fishing XP from spears, harpoons and bows used in water | SpearFishing |
| Swimming | Swim speed and swim stamina drain | VikingsDoSwim |
| Taming, Blood Magic, Blocking, Bees and more | A large set of milestone bonuses layered on the vanilla skills | ImpactfulSkills |

### The doubled yields

Hrafnheim doubles gathering yields server-wide. **These are not flat multipliers.** Every one of
them is the factor **at skill level 100**, scaling up from 1× at level 0. A level 20 miner sees
roughly a 1.2× yield, not 2×.

| Setting | Value | What it means at level 100 |
| --- | --- | --- |
| Mining Yield Factor | 2 | Twice the ore per deposit |
| Tree item yield modifier | 2 | Twice the wood per tree |
| Crop Yield Factor | 2 | Twice the harvest per plant |
| Foraging Yield Factor | 2 | Twice the berries, mushrooms and thistle per pick |
| Multiplier for Respawn Speed (Foraging) | 2 | Pickables come back twice as fast |

Two related factors are set to **3** at level 100: **Mining Damage Factor** and **Damage to trees
modifier**, so ore and trees break faster as well as giving more. Farming also runs **Grow Speed
Factor 3**, so crops mature up to three times faster at level 100.

All eight of those values happen to equal the mods' own defaults. What makes them a server fact is
that every one of these configs is `[Synced with Server]` with `Lock Configuration = On`: the server
decides them and your local edit is ignored. Vanilla Valheim has no yield scaling at all, so the
doubling is real relative to unmodded play — it just was not tuned upward from the mod baseline.

Experience gain is **not** boosted: every skill mod here runs `Skill Experience Gain Factor = 1`, and
`Skill Experience Loss = 0`, so these skills are not lost on death (the 5% general skill loss from
DeathPenalty still applies to vanilla skills).

### Area gathering

| Key | Action | Mod |
| --- | --- | --- |
| `N` | Toggle AOE harvesting, AOE mining and AOE planting on/off | ImpactfulSkills (default) |
| `Left Shift` (hold) | Bulk harvest | PlantEasily (default) |
| `Left Control` + `T` | Toggle the explosive-mining chance (stand still) | Smoothbrain Mining (default) |

Foraging's mass picking reaches **10 m at level 100** and needs no key. Mining's explosion chance
unlocks at level 50 and is on by default.

<!-- guide:vr -->

Foraging's mass picking needs no key and works normally in VR. Explosive mining is on by default from
level 50, so that one is already running too. The AOE toggle is not on the wrist menu, and the
bulk-harvest hold and the explosive-mining chord cannot be pulsed, so from a headset you take these
three systems as they are currently set.

<!-- /guide:vr -->

---

## 7. Farming, taming, riding and boats

### Planting

Two systems overlap here and it is worth knowing which is which.

**PlantEasily** gives you a grid: place several plants in one action, with snapping and automatic
replanting on harvest.

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Right Arrow` / `Left Arrow` | More / fewer grid columns | PlantEasily (default) |
| `Up Arrow` / `Down Arrow` | More / fewer grid rows | PlantEasily (default) |
| `Right Control` (hold) | Keyboard modifier for the grid controls | PlantEasily (default) |
| `Left Shift` (hold) | Bulk harvest | PlantEasily (default) |
| `F6` | Toggle replant-on-harvest | PlantEasily (default) |
| `F10` | Toggle piece snapping | PlantEasily (default) |
| `F8` | Turn every PlantEasily feature off and on | PlantEasily (default) |

`F6`, `F8` and `F10` are all contested keys. See the conflicts block before you rely on them.

**Smoothbrain Farming** has its own mass-plant mode, toggled while standing still:

| Key | Action | Mod |
| --- | --- | --- |
| `Left Shift` | Toggle between single-plant and mass-plant mode (stand still) | Smoothbrain Farming (default) |
| `Left Control` | Toggle snapping mode (stand still) | Smoothbrain Farming (default) |

<!-- /guide:flat -->

<!-- guide:vr -->

**PlantEasily's grid keys and its master toggle have no VR route at all.** The grid controls are arrow
keys behind a held modifier, and the three feature toggles are multi-claimed function keys that a
wrist pulse would fire on several mods at once, so none of them is on the wrist menu. Smoothbrain
Farming's mass-plant and snapping toggles are holds and are equally out of reach. Whatever grid,
snapping and replant-on-harvest settings are currently active stay active: from a headset you plant by
hand, one action at a time, and cannot change them.

<!-- /guide:vr -->

PlantEverything additionally lets you plant things vanilla will not: birch and oak, berry bushes,
thistle, mushrooms, dandelion, pickable flint and stone, and vines.

### Fermenters, kilns and fires

- Fermenters empty themselves when done (AutomaticFermenters).
- Kilns, smelters and furnaces refuel themselves from nearby containers (AutomaticFuel). Toggle the
  behaviour with `F10` (default) — the same key PlantEasily uses for snapping, so expect both to
  fire.
- Torches, sconces and candles never go out (TimedTorchesStayLit, CandlesForever).
- Hold `Left Alt` while interacting with a **player-built** fireplace to toggle infinite fuel on it
  (ZenWorldSettings, default).

<!-- guide:vr -->

**Infinite Fuel** is on the Fireplace hover menu: point the laser at a player-built fireplace and
hold the off-hand grip, and the plugin performs the `Left Alt` hold for you. Automatic fuelling of
kilns, smelters and furnaces is already on and its toggle is not on the wrist menu, so leave that one
alone. Nothing else in this group needs input.

<!-- /guide:vr -->

### Taming and breeding

Vanilla tameables only. Boar, wolves, lox, hens and asksvin are tamed and bred as normal, with three
changes:

- Taming is roughly **6× faster** and tamed animals drop **3× loot** (ImpactfulSkills).
- Breeding is reworked: offspring inherit and the process is controllable (ZenBreeding).
- Bees improve with skill — better honey output from level 15, and from level 25 hives are no longer
  restricted by biome (ImpactfulSkills).

There is **no all-creatures-tameable mod installed**. A stale `AllTameable` config remains in the
config directory from a previous profile; ignore it. Its `G` / `H` mass-command keys do nothing.

### Riding

<!-- guide:flat -->

Riding is **vanilla only**: saddle a lox or an asksvin and steer with the mouse and `W`/`A`/`S`/`D`.
`Space` dismounts.

<!-- /guide:flat -->

<!-- guide:vr -->

Riding works, but not from the keyboard. Push the physical **left** thumbstick forward to move and
left or right to turn, so your head stays free to look around. **Release Mount** on the wrist radial
menu dismounts. The Horse hover group — Wait Here, Saddlebags, Remove Armour — is dead: it points at
OdinHorse, which is not installed.

<!-- /guide:vr -->

Odin's Horse Pen is installed, but it is a **build piece set only** (Misc tab). The OdinHorse mod
that added rideable horses with saddlebags is **not installed** on this profile — it was removed on
13 August. Its config file survives in the config directory and its `R` / `T` / `B` keys do nothing.
If you read a horse binding anywhere else, that is why.

### Boats

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Left Shift` + `F` while your mouse is over the rudder | Raise or drop the anchor | BoatAdditions (default) |

Sail control is vanilla: `W` raises sail, `S` lowers it, `A`/`D` steer, `E` to take or release the
helm.

<!-- /guide:flat -->

<!-- guide:vr -->

Point the laser at the ship and hold the **off-hand grip**: the Ship hover menu carries **Anchor**,
**Sail Faster**, **Sail Slower** and **Release Helm**. Sail Faster and Sail Slower also appear on the
wrist radial menu while you are at the helm. Steering is gestured rather than keyed, so sailing is one
of the better-covered systems in VR.

<!-- /guide:vr -->

Other ship changes:

- Boat Additions adds extra hulls, buildable from the **BoatAdditions** hammer category (fine wood,
  stone, resin, deer hide, bronze).
- **Longship upgrades**: at a level 4 workbench a longship can be widened to 7 container slots
  (elder bark, silver, obsidian), and at an artisan station heightened to 4 rows (black metal,
  yggdrasil wood, fine wood), taking it to 1500 health. Only the ship's creator can upgrade it or
  change its trophy.
- **Shipwright** repairs ships in the field: 10 wood and 5 tin to craft at a forge, 1 wood and 5
  stamina per repair tick.
- Boats and carts take no damage and no water damage at all (AzuWearNTearPatches), so the repair
  tooling is for convenience rather than survival.
- Carts can be crafted with storage and towed behind a boat (CraftyCartsRemake).

### Swimming

<!-- guide:flat -->

| Key | Action | Mod |
| --- | --- | --- |
| `Left Shift` (hold) | Swim faster | VikingsDoSwim (default) |
| `Left Control` (hold) | Dive underwater | VikingsDoSwim (default) |
| `Space` | Ascend to the surface | VikingsDoSwim (default) |

<!-- /guide:flat -->

<!-- guide:vr -->

Swimming faster, diving and ascending are all held keys and have **no VR route**. VHVR's gestured
locomotion covers swimming itself, so you swim by moving; you simply cannot trigger VikingsDoSwim's
sprint, dive or ascend from a headset, which in practice means diving gear is of limited use to you.

<!-- /guide:vr -->

Diving gear is craftable from 1 ruby, 10 silver, 10 iron and 10 bronze. Underwater fog and colour
are adjusted so you can actually see. You keep your equipment wielded in water.

---

## 8. Map, pins and exploration

### Automatic pins

SullysAutoPinner scans a **300 m radius every 12 seconds** and pins things for you, merging pins
within 50 m. It is deliberately tuned to pin things worth walking to and not to spam the map with
berries.

**Pinned:** copper, tin is off but iron, silver, obsidian, mudpiles, meteorite and excavation sites
are on; troll caves, crypts, dwarf spawners, totems, fires; tar pits, dragon eggs, vulture eggs;
mage caps, yggdrasil cones, jotun puffs; Mistlands giants, giant swords, ribs, skulls and brains,
Dverger things, lanterns; abominations, bonemaw serpents, seeker brutes; Ashlands pots, shipwreck
chests; map markers.

**Not pinned:** mushrooms, raspberries, blueberries, cloudberries, thistle, seeds, barley, flax,
skeletons, draugr spawners, treasure chests, smoke puffs, tin.

Pins are saved every 120 seconds. There is no hotkey; it just runs.

<!-- guide:flat -->

Connected portals are tagged on the map. Hold `Left Shift` and press your use key on a portal to
open the Tag Connected Portals dialog (TagConnectedPortals, default).

<!-- /guide:flat -->

<!-- guide:vr -->

Connected portals are tagged on the map for everyone. Opening the Tag Connected Portals dialog needs
`Left Shift` held while you interact, no hover group covers portals, and a chord cannot be pulsed, so
that dialog is **not reachable in VR**. Portals themselves work normally; only the tagging dialog is
out of reach.

<!-- /guide:vr -->

### The shared map page

`https://valheim.neuralyze.com/worlds/Hrafnheim/map`

Signed in with the same Steam account that gets you into the world. It draws terrain and biomes,
locations, player construction coloured and named by builder, player pins, and — importantly — a fog
of war built from **what players have actually revealed**, not from what the server happened to
generate. Layers are individually toggleable, and you can view the map through one character's eyes
instead of the shared union, which keeps an admin's travels from spoiling the map for everyone else.

### The exploration reporter

Your client carries a small portal plugin (`NeuralyzeExplorationReporter`) that reads your own
minimap fog and your own pins and writes them to a file. It writes on every player-profile save and
again when you log out, and uploads at logout. If an upload cannot happen at that moment, the next
sync sends it instead.

Two properties are worth knowing:

- The files live **beside** your profile, not inside `active/`, so a sync cannot delete your map.
- Reports are marked once accepted, so an unchanged map is not re-sent on every launch.
- Each character keeps its own map: two characters on one account produce two reports.

The upload is authorised by a narrow token that can do nothing except upload a map. It carries your
revealed area and your pins, and nothing else.

---

## 9. Wards and permissions

A ward (guard stone) claims an area. Everything below is `[Synced with Server]` and the server
forces its config, so these rules are the same for everybody.

| Key | Action | Mod |
| --- | --- | --- |
| `F4` | Toggle your permission on a ward you are permitted on | WardIsLove (shipped) |

`F4` is also IdentityCrisis's transformation UI key, which was not moved. Expect both to respond.

<!-- guide:vr -->

Point the laser at the ward and hold the **off-hand grip**: the Ward hover menu's **Toggle
Permission** pulses the ward key for you, and the same entry sits on the wrist radial menu as
**Identity**. Either route pulses `F4`, so either route also opens IdentityCrisis's transformation UI.
That collision is unavoidable from a headset; close the UI and carry on.

<!-- /guide:vr -->

### What a ward stops a non-permitted player doing

Inside somebody else's ward, if you are not on its permission list, you **cannot**:

- open chests
- open or close doors
- use crafting stations
- pick crops or other pickables
- pick up items dropped on the ground
- pick up items automatically
- use a portal
- use item stands, map tables, signs, beehives, smelters or ships

Structures inside a ward take **no weather damage**. Indestructible structures are switched off, and
ward damage to intruders is set to 0 — a ward here protects property, it does not attack you.

PvE is not forced inside wards; PvP flags are unchanged.

### Who can build where

- Build outside anybody else's ward, or inside one you have been given permission on.
- A ward's owner adds you by interacting with the stone. Admins are **not** auto-permitted
  (`AdminAutoPerm = false`), so an admin has to be added like anyone else.
- **Maximum 3 wards per player** (5 for players on the VIP list).
- A ward **deactivates after 30 days**. Log in and touch your wards if you go away for a month.
- You are told when you enter and leave a ward, with the owner's name.

---

## 10. VR players

Both flat and VR players share Hrafnheim. The flat profile still loads `ValheimVRMod` with
`nonVrPlayer = true`, so VR bindings exist in the config even in flat play; they simply do nothing
without a headset.

<!-- guide:flat -->

If you find VHVR keys in your own config files — the arrow keys, `PageUp`/`PageDown` and `Home` —
that is why. They are loaded and inert without a headset, and they are documented in the VR edition of
this guide rather than here.

<!-- /guide:flat -->

<!-- guide:vr -->

### Keyboard keys VHVR itself uses

| Key | Action | Source |
| --- | --- | --- |
| Arrow keys | Move the head camera forward / back / left / right | ValheimVRMod (default) |
| `PageUp` / `PageDown` | Move the head camera up / down | ValheimVRMod (default) |
| `Home` | Recentre the headset | ValheimVRMod (default) |

Head repositioning is enabled and the headset recentres on start. Player height is adjusted by
−0.2 m in the shipped config. Arrow keys and `PageUp`/`PageDown` collide with Infinity Hammer's
precise-placement nudges, so a VR admin holding a hammer will move both the ghost and their head.

### How VR actually plays here

Read from the live VHVR config:

- Smooth turn, not snap turn. Run is a toggle. The character moves with the headset.
- Sneak is crouching or controller. Gestured locomotion covers swimming and steering.
- Dominant hand right; two-handed weapons use polearm-sticky wield; spears throw in classic mode.
- **Blocking is gestural** — you physically raise the shield, there is no block key.
- Bows are two-handed with full draw restriction and accuracy that ignores draw length, with an
  arrow prediction graphic. Crossbows reload manually.
- Attack damage does **not** scale with swing momentum; a minimum swing speed of 3 is required.
- VHVR's own Advanced Build Mode is off; PerfectPlacement is used instead.

### The problem VR has with a modded server, and how it is solved

Most of the mod keys in this guide are keyboard-only, and a lot of them are "hold a key while
interacting with a specific object". Neither survives a headset. The profile carries a purpose-built
plugin (`NeuralyzeVRFixes`) that closes the gap in two ways.

**A wrist radial menu** on the **left** wrist (`MenuHand = Left`; the value is empirical, VHVR's
naming does not match the physical controllers). It carries:

| Entry | What it does |
| --- | --- |
| Close Panel | Escape hatch — deactivates adopted canvases, hides the main menu, pulses Escape |
| Release Mount | Dismount |
| Sail Faster / Sail Slower | Only shown while at the helm |
| Identity | `F4` |
| Cycle Spell | `Keypad1` |
| Unsummon | `Keypad2` |
| Hip Lantern | `Keypad3` |
| Cycle Power | `Keypad4` |
| Admin/… | Admin console, admin panel and a set of spawn and cheat commands, hidden from non-admins |

**A contextual hover menu.** Point the laser at a thing, hold the **off-hand grip**, and options for
that kind of object appear; tap the off-hand grip to move to the next; release to run the highlighted
one.

| Target | Options |
| --- | --- |
| Container | Quick Stack, Restock, Sort, Store All |
| Ward | Toggle Permission (`F4`) |
| Fireplace | Infinite Fuel (holds `Left Alt` while interacting) |
| Ship | Sail Faster, Sail Slower, Release Helm, Anchor (`Left Shift` + `F`) |
| Piece | Repair Area (`Left Shift` + `W`), Add Wear (`Left Alt` + `W`) |
| Horse | Wait Here, Saddlebags, Remove Armour — **dead entries**, see below |

Mounts are steered by the physical **left** thumbstick (`HorseStick = VhvrRight`, again an empirical
name), pushing forward to go and left/right to turn, so your head stays free.

The VR fixes config also carries a Companions HUD placement setting (`RightWrist`), but the
Companions mod is not installed on this profile, so that setting does nothing.

### Controls a VR player cannot reach

This is the honest list.

- **Every held modifier and chord.** A wrist button is a one-frame pulse and cannot represent a hold.
  That rules out, from the headset: VikingsDoSwim sprint/dive/ascend, the Wearable Trophies equip
  modifiers, RequipMe, RuinsMaker's hammer modifier, Infinity Hammer's selection and command
  modifiers, the AzuCraftyBoxes fill-all modifier, the AAA_Crafting scroll modifiers, the favouriting
  modifiers in both storage mods, and the PlantEasily grid modifier. The three that mattered most
  (fireplace fuel, area repair, add wear) were re-implemented as **hover** actions, which can hold a
  key and interact in the same frame.
- **`F6`, `F8`, `H`, `Period`, `P`, `L`, `O`** are deliberately absent from the wrist menu because
  each is claimed by more than one mod and a pulse would fire all of them. Quick stack, restock, sort
  and store-all are reachable via the container hover menu instead.
- **PlantEasily's grid keys and `F8` master toggle** have no VR route at all.
- **The AdminQoL panel** is a screen-space canvas. It can be converted to world space on request via
  the wrist entry, but it is not opened by its own hotkey path in VR.
- **Terrain-tool radius and hardness** (`Left Alt`/`Left Control` + scroll) have no VR route.
- **The horse hover group is dead.** It references `Keypad6`, `B` and `R` for a mod (OdinHorse) that
  is no longer installed. Those three options will appear if you point at a tameable named horse and
  do nothing.

### VR coverage that is not established

- Whether the EpicLoot ability keys (`G`, `H`, `J`) are reachable in VR. They are not in the wrist
  menu and no hover group covers them. EpicLootVRFix is installed, which suggests EpicLoot's UI is
  handled, but nothing in the config establishes how an ability is fired from a headset.
- Whether the quick-slot keys (`Alt` + `Z`/`X`/`C`) can be triggered in VR. They are chords, so by
  the rule above they cannot be pulsed, and the on-screen quick-slot strip is switched off.
- Whether `Q` alternate-interact behaves correctly in VR given Infinity Hammer also binds `Q`.
- Whether the arrow-key/`PageUp`/`PageDown` head repositioning is usable at all in practice while
  Infinity Hammer is loaded.

<!-- /guide:vr -->

---

## 11. Admin appendix

**Admin only.** Everything in this section requires your Steam ID on the server admin list. Non-admin
players can press these keys and type these commands; the server refuses them.

<!-- guide:vr -->

**From a headset.** The wrist radial menu's **Admin/…** group carries the admin console, the admin
panel and a set of spawn and cheat commands, and is hidden from non-admins. The AdminQoL panel is a
screen-space canvas: it can be converted to world space from that wrist entry, but it is not opened by
its own hotkey path in VR. Typing console commands still needs a physical keyboard you cannot see.
Take particular care with Infinity Hammer below: its arrow-key and `PageUp`/`PageDown` nudges are the
same keys VHVR uses to reposition your head camera, so a VR admin holding a hammer moves the ghost and
their own viewpoint at once. Map teleport is a chord and cannot be pulsed either. Everything else in
this appendix is console work and reads the same in both editions of this guide.

<!-- /guide:vr -->

### The console

**`F5`** opens Valheim's console. The launcher passes `-console` on every launch, so it is always
available and you do not need a Steam launch option.

**`KeypadDivide`** toggles the AdminQoL console panel while the `F5` console is visible — a clickable
900×450 panel with three favourite command tabs, a released mouse cursor and YAML-driven item sets
injected into the vanilla `itemset` command. AdminQoL also grants admins, and only admins:

- crafting stations ignore roof and exposure checks (fire requirements still apply)
- no armour durability loss from taking damage
- no item durability loss from attacking, blocking, building, repairing or passive drain

`G` toggles or holds the prefab inspector and `H` copies the inspection snapshot to the clipboard.
Both collide with EpicLoot ability keys.

### Server Devcommands

Extends the console: aliases (`alias.yaml`), key binds (`binds.yaml`), permissions
(`permissions.yaml`) and hundreds of extra commands, all usable on a dedicated server rather than
only in single-player. Notable server settings in effect: `Automatic devcommands = true`,
`Automatic item pick up = true`, `Kill destroys spawners = true`, `Debug mode fast teleport = true`,
`No stamina usage with god mode = true`.

`Left Control` + middle-click on the map teleports you. `/devcommands` context must be enabled first.
Two mods bind this: Server Devcommands (`Mouse2 + LeftControl`) and Zen.ModLib
(`Map Teleport Key = LeftControl`).

### Infinity Hammer

Selects, copies, moves and stamps anything in the world. **Ships no config file**, so it runs on its
documented defaults; the generated config on the client matches them.

| Key | Action |
| --- | --- |
| `Keypad5` | Select the object you are looking at |
| `Left Control` + `Keypad5` | Select and freeze |
| `Left Alt` + `Keypad5` | Pick the object |
| `Keypad0` | Freeze the placement position |
| `PageUp` / `PageDown` | Nudge up / down (`Left Alt` for a large step) |
| Arrow keys | Nudge horizontally and forward/back (`Left Alt` for a large step) |
| `Left Shift` + scroll | Scale the selection, and change command height |
| `Left Shift` + `Left Alt` + scroll | Change the command rectangle depth |
| `Left Shift` + `Left Control` + scroll | Change the command rotation |
| `Q` | Change the selection shape |
| `Keypad7` | Undo |
| `Keypad9` | Redo |
| `Left Alt` / `Left Control` | Command modifiers 1 and 2 |

Zooping, the grid toggles and every build-menu entry (`Menu: Blueprints`, `Menu: Objects`,
`Menu: Rooms`, and the rest) are **unbound**. Bind them yourself in the config if you want them;
nothing on this server ships a binding for them.

### PerfectPlacement Advanced Editing Mode

This is the tool for moving something that is already built.

| Key | Action |
| --- | --- |
| `Keypad1` | Enter Advanced Editing Mode on the object you are looking at |
| move / rotate | mouse and scroll; `KeypadPlus` / `KeypadMinus` change the step |
| `Keypad3` / `Keypad8` | Copy / paste the object's rotation |
| `F7` | Reset it to where it started |
| `F8` | Abort and exit, resetting the object |
| `KeypadEnter` | Confirm and place |

**This relocates a placed chest with its contents intact.** `Keypad1` on the chest, move it,
`KeypadEnter`. Nothing is dropped and nothing has to be emptied first. It works the same way on
other containers, on crafting stations and on smelters.

`Keypad1` also cycles spells (MagicRevamp) and `Keypad3` also toggles the hip lantern (HipLantern).
Both of those are shipped values, so expect the side effects.

### Structure Tweaks

Server-side control over how individual pieces behave: what is indestructible, what needs support,
what can be interacted with, growth and spawn behaviour, and per-prefab overrides. Configured by
`structure_tweaks.cfg` on the server; it has **no keys**.

### World Edit Commands

Console commands for bulk object operations: `object`, `terrain`, `spawn_object`, `location` and
friends, with filters by prefab, radius, rectangle and rotation. All console; no keys.

### Upgrade World

Console commands for world-scale maintenance: regenerating zones and locations, resetting
destructibles and vegetation, moving or wiping regions, and re-running world generation over an
existing save. Destructive. All console; no keys.

### SkToolbox

**Not installed on this profile.** A `com.Skrip.SkToolbox.cfg` file remains in the server config
directory from an earlier profile, but no SkToolbox plugin is present on either the server or the
client. Its `PageUp`/`PageDown`/arrow-key menu navigation therefore does not exist here, and the
three-way collision on those keys that older notes describe is now a two-way collision between
Infinity Hammer and VHVR.

### Other admin tooling present

- **Valheim Rcon** — remote console access to the server process.
- **Serverside Simulations** — moves parts of the simulation server-side.
- **ConditionalConfigSync** — pushes conditional config to clients.
- **Item Stand All Items** / **ZenItemStands** — any item on any stand.
- **Ballista Infinite Ammo**, **FearMe**, **YouAreBeingWatched**, **IdentityCrisis** — behaviour and
  cosmetic tools, no admin gating.
- **LoadTimeProfiler**, **NeuralyzeVRFixes** diagnostics sections — measurement only, shipped off.

---

## 12. Key reference table

Sorted by key. Provenance marker as defined at the top of this guide. Vanilla rows are the game's
own defaults, rebindable in **Settings → Controls**, and are **not** read from any file on this
server.

ValheimVRMod is loaded in the flat profile too (`nonVrPlayer = true`), so it appears in the conflict
notes below as a claimant on the arrow keys and `PageUp`/`PageDown` even though its own bindings do
nothing without a headset.

| Key | Action | Owner | Provenance |
| --- | --- | --- | --- |
| `1`–`8` | Hotbar slots | Valheim | vanilla default |
| `Arrow keys` | Nudge the Infinity Hammer selection horizontally / forward-back (large step with `Left Alt`) | Infinity Hammer | documented default |
| `Arrow keys` | Grow / shrink the planting grid (`Right`/`Left` columns, `Up`/`Down` rows) | PlantEasily | default |
| `C` | Rotate the placement ghost on X | PerfectPlacement | shipped |
| `Ctrl` + `F3` | Hide the HUD | Valheim | vanilla default |
| `Delete` | Trash the item under the cursor | Quick Stack Store Sort Trash Restock | default |
| `E` | Use / interact | Valheim | vanilla default |
| `E` | Cancel a bow draw | BowsBeforeHoes, VHModpackFix | default |
| `E` | Claim a bed | BedRules | default |
| `F` | Toggle the favourite-recipe filter | AAA_Crafting | shipped |
| `F` | Copy the rotation of the piece in front of you (parallel) | PerfectPlacement | shipped |
| `F1` | Enter Advanced Building Mode | PerfectPlacement | shipped |
| `F2` | Skills | Valheim | vanilla default |
| `F3` | Exit Advanced Building Mode | PerfectPlacement | shipped |
| `F4` | Toggle your permission on a ward | WardIsLove | shipped |
| `F4` | Open / close the transformation UI | IdentityCrisis | default |
| `F5` | Open / close the game console | Valheim (`-console` passed by the launcher) | launcher source |
| `F6` | Change the default build-grid alignment | PerfectPlacement | shipped |
| `F6` | Toggle replant-on-harvest | PlantEasily | default |
| `F6` | Show / hide the comfort-pieces list | ComfortTweaks | default |
| `F7` | Reset the moved object to its original position | PerfectPlacement | shipped |
| `F7` | Toggle build-grid alignment | PerfectPlacement | shipped |
| `F8` | Abort and exit Advanced Editing Mode | PerfectPlacement | shipped |
| `F8` | Toggle every PlantEasily feature | PlantEasily | default |
| `F9` | Reset your Forsaken Power cooldown | ForsakenPowersPlusRemastered | shipped |
| `F10` | Toggle automatic fuelling | AutomaticFuel | default |
| `F10` | Toggle planting-grid snapping | PlantEasily | default |
| `G` | EpicLoot ability slot 1 | EpicLoot | shipped |
| `G` | Copy the rotation of the piece in front of you (perpendicular) | PerfectPlacement | shipped |
| `G` | Prefab inspector — **admin** | AdminQoL | shipped |
| `H` | EpicLoot ability slot 2 | EpicLoot | shipped |
| `H` | Copy the inspection snapshot — **admin** | AdminQoL | shipped |
| `J` | EpicLoot ability slot 3 | EpicLoot | shipped |
| `Keypad0` | Freeze the placement position | Infinity Hammer | documented default |
| `Keypad1` | Enter Advanced Editing Mode — **admin** | PerfectPlacement | shipped |
| `Keypad1` | Cycle to the next spell in your inventory | MagicRevamp | shipped |
| `Keypad2` | Unsummon your summons | MagicRevamp | shipped |
| `Keypad3` | Copy the selected object's rotation (ABM / AEM) | PerfectPlacement | shipped |
| `Keypad3` | Toggle the equipped hip lantern | HipLantern | shipped |
| `Keypad4` | Cycle your Forsaken Power | ForsakenPowersPlusRemastered | shipped |
| `Keypad5` | Select the object you are looking at | Infinity Hammer | documented default |
| `Left Alt` + `Keypad5` | Pick the object you are looking at | Infinity Hammer | documented default |
| `Left Control` + `Keypad5` | Select the object and freeze it | Infinity Hammer | documented default |
| `Keypad7` | Undo the last hammer action | Infinity Hammer | documented default |
| `Keypad8` | Paste the copied rotation (ABM / AEM) | PerfectPlacement | shipped |
| `Keypad9` | Redo the last undone hammer action | Infinity Hammer | documented default |
| `KeypadEnter` | Confirm the placement in Advanced Editing Mode | PerfectPlacement | shipped |
| `KeypadPlus` / `KeypadMinus` | Increase / decrease the ABM / AEM move and rotate step | PerfectPlacement | shipped |
| `KeypadDivide` | Toggle the AdminQoL console panel while `F5` is open — **admin** | AdminQoL | shipped |
| `L` | Restock ammo and consumables | Quick Stack Store Sort Trash Restock | default |
| `Left Alt` | Dodge while moving | Valheim | vanilla default |
| `Left Alt` (hold) | Rotate the placement ghost on Y, and align to the build grid | PerfectPlacement | shipped |
| `Left Alt` (hold) | Toggle infinite fuel on a player-built fireplace, while interacting | ZenWorldSettings | default |
| `Left Alt` (hold) | Hide the raid perimeter rings | ZenRaids | default |
| `Left Alt` (hold) | Add wear-and-tear to the piece you hammer | RuinsMaker | default |
| `Left Alt` (hold) | Large-step modifier and command modifier 1 | Infinity Hammer | documented default |
| `Left Alt` + scroll | Change the terrain-tool radius | AdvancedTerrainModifiers | default |
| `Left Alt` / `Right Alt` (hold) | Favourite an item or slot against stack / sort / trash | Quick Stack Store Sort Trash Restock | default |
| `Left Alt` + `H` | Toggle the hip-lantern heat aura | HipLantern | shipped |
| `Left Alt` + `O` | Turn off all pulling from nearby containers | AzuCraftyBoxes | default |
| `Left Alt` + `W` | Apply wear-and-tear in a radius | RuinsMaker | default |
| `Left Alt` + `Z` | Quick slot 1 | AzuExtendedPlayerInventory | default |
| `Left Alt` + `Z` | Re-equip manually | RequipMe | default |
| `Left Alt` + `X` | Quick slot 2 | AzuExtendedPlayerInventory | default |
| `Left Alt` + `C` | Quick slot 3 | AzuExtendedPlayerInventory | default |
| `Left Control` | Crouch / sneak | Valheim | vanilla default |
| `Left Control` (hold) | Show the minus button on recipe icons | AAA_Crafting | shipped |
| `Left Control` (hold) | Dive while swimming | VikingsDoSwim | default |
| `Left Control` (hold) | Equip a weapon to your left hand | Wearable Trophies | default |
| `Left Control` (hold) | Switch ZenRedecorate into move mode | ZenRedecorate | default |
| `Left Control` (hold) | Toggle planting-snap mode, standing still | Smoothbrain Farming | default |
| `Left Control` (hold) | Command modifier 2 | Infinity Hammer | documented default |
| `Left Control` + scroll | Jump the craft amount to the maximum you can afford | AAA_Crafting | shipped |
| `Left Control` + scroll | Change the terrain-tool hardness | AdvancedTerrainModifiers | default |
| `Left Control` + `Left Alt` | Equip a weapon to your left back | Wearable Trophies | default |
| `Left Control` + `P` | Open the Traders Extended editor | TradersExtended | default |
| `Left Control` + `T` | Toggle explosive mining, standing still | Smoothbrain Mining | default |
| `Left Control` + middle-click on the map | Teleport — **admin** | Server Devcommands, Zen.ModLib | default |
| `Left Shift` | Sprint | Valheim | vanilla default |
| `Left Shift` (hold) | Pull all available fuel or ore from containers | AzuCraftyBoxes | default |
| `Left Shift` (hold) | Quiver bar modifier | BowsBeforeHoes | default |
| `Left Shift` (hold) | Swim faster | VikingsDoSwim | default |
| `Left Shift` (hold) | Equip a trophy or weapon as style only | Wearable Trophies | default |
| `Left Shift` (hold) | Bulk-harvest modifier | PlantEasily | default |
| `Left Shift` (hold) | Toggle mass-plant mode, standing still | Smoothbrain Farming | default |
| `Left Shift` (hold) | Keep pulling to the target while the harpoon flies | HarpoonExtended | default |
| `Left Shift` + scroll | Increase the craft amount in steps of 10 | AAA_Crafting | shipped |
| `Left Shift` + scroll | Scale the selection, and change the command height | Infinity Hammer | documented default |
| `Left Shift` + `Left Alt` | Equip a weapon to your right back | Wearable Trophies | default |
| `Left Shift` + `Left Alt` + scroll | Change the command rectangle depth | Infinity Hammer | documented default |
| `Left Shift` + `Left Control` + scroll | Change the command rotation | Infinity Hammer | documented default |
| `Left Shift` + `Left Control` + `T` | Stop harpooning | HarpoonExtended | default |
| `Left Shift` + `E` | Alternate interact | Valheim | vanilla default |
| `Left Shift` + `E` | Open the Tag Connected Portals dialog | TagConnectedPortals | default |
| `Left Shift` + `F` | Raise or drop the anchor, mouse over the rudder | BoatAdditions | default |
| `Left Shift` + `PageUp` | Toggle the crafting Recipe UI | AAA_Crafting | shipped |
| `Left Shift` + `Period` | Pause automatic storing | AzuAutoStore | shipped |
| `Left Shift` + `W` | Repair every piece in a radius | RuinsMaker | default |
| `M` | Map | Valheim | vanilla default |
| `Mouse0` | Attack | Valheim | vanilla default |
| `Mouse0` | Pick up, in ZenRedecorate move mode | ZenRedecorate | default |
| `Mouse1` | Block / secondary attack, and open the build menu | Valheim | vanilla default |
| `Mouse1` | Zoom while drawing a bow | BowsBeforeHoes, VHModpackFix | default |
| `Mouse2` | Map ping | Valheim | vanilla default |
| `Mouse2` | Store the clicked item into nearby containers | AzuAutoStore | shipped |
| `N` | Toggle AOE harvesting, mining and planting | ImpactfulSkills | default |
| `O` | Sort the open container or your inventory | Quick Stack Store Sort Trash Restock | default |
| `P` | Quick stack into nearby containers | Quick Stack Store Sort Trash Restock | default |
| `PageUp` / `PageDown` | Nudge the selection up / down (large step with `Left Alt`) | Infinity Hammer | documented default |
| `Period` | Store your inventory into nearby containers | AzuAutoStore | shipped |
| `Q` | Alternate interact, instead of `Left Shift` + `E` | Zen.ModLib | default |
| `Q` | Change the Infinity Hammer selection shape | Infinity Hammer | documented default |
| `Return` | Chat | Valheim | vanilla default |
| `Right Control` (hold) | Keyboard modifier for the planting grid | PlantEasily | default |
| `Space` | Jump | Valheim | vanilla default |
| `Space` | Ascend to the surface while swimming | VikingsDoSwim | default |
| `T` | Pull a harpooned target closer | HarpoonExtended | default |
| `Tab` | Inventory | Valheim | vanilla default |
| `V` | Rotate the placement ghost on Z | PerfectPlacement | shipped |
| `W` `A` `S` `D` | Move | Valheim | vanilla default |
| `X` | Sit | Valheim | vanilla default |
| `Y` (hold) | Search nearby chests for the item you click | AzuAutoStore | shipped |
| `Z` (hold) | Favourite an item so auto-store leaves it alone | AzuAutoStore | shipped |
| `JoystickButton3` / `8` / `9` | Gamepad: Stats / Vanity / Loadout panels | AzuExtendedPlayerInventory | default |
| `JoystickButton4` | Gamepad modifier for the planting grid | PlantEasily | default |

<!-- guide:vr -->

#### Keys VHVR itself owns

These three are ValheimVRMod's own bindings, and they are the only rows in this reference that a flat
player has no use for. They are present in the flat profile as well, where they do nothing.

| Key | Action | Owner | Provenance |
| --- | --- | --- | --- |
| `Arrow keys` | Reposition the VR head camera | ValheimVRMod | default |
| `Home` | Recentre the headset | ValheimVRMod | default |
| `PageUp` / `PageDown` | Move the VR head camera up / down | ValheimVRMod | default |

Both the arrow keys and `PageUp`/`PageDown` are also Infinity Hammer's precise-placement nudges, so an
admin holding a hammer moves the ghost and their head at the same time.

<!-- /guide:vr -->

### Key conflicts

**A key claimed by two mods fires both.** These are the live collisions on this profile, counting mod
keys only and excluding vanilla bindings.

| Key | Claimed by | Why it matters |
| --- | --- | --- |
| `F4` | WardIsLove (ward permission), IdentityCrisis (transformation UI) | Toggling a ward also opens the transformation UI |
| `F6` | PerfectPlacement (default grid alignment), PlantEasily (replant-on-harvest), ComfortTweaks (comfort list) | Three-way. Avoid `F6`; this exact key opened an unconverted screen-space canvas in VR and broke a session |
| `F8` | PerfectPlacement (abort Advanced Editing Mode), PlantEasily (master toggle) | Aborting an edit also silently disables every PlantEasily feature |
| `F10` | AutomaticFuel (toggle auto-fuel), PlantEasily (snapping) | Both fire on one press |
| `G` | EpicLoot (ability 1), PerfectPlacement (perpendicular rotation), AdminQoL (prefab inspector, admin) | Three-way. Firing an ability while holding a hammer also rotates the ghost |
| `H` | EpicLoot (ability 2), AdminQoL (copy snapshot, admin) | Ability 2 also copies to the clipboard for admins |
| `Keypad1` | PerfectPlacement (enter Advanced Editing Mode), MagicRevamp (cycle spell) | **New**, created by moving PerfectPlacement off `Keypad0`. Both values are shipped |
| `Keypad3` | PerfectPlacement (copy object rotation), HipLantern (toggle lantern) | **New**, created by moving PerfectPlacement off `Keypad7`. Both values are shipped |
| `Q` | Zen.ModLib (alternate interact), Infinity Hammer (change selection shape) | Interacting while holding a hammer also changes the selection shape |
| `E` | BowsBeforeHoes / VHModpackFix (cancel bow draw), BedRules (claim bed) | Only overlaps if you are drawing a bow next to an unclaimed bed |
| `Left Alt` + `Z` | AzuExtendedPlayerInventory (quick slot 1), RequipMe (manual re-equip) | Using quick slot 1 also triggers a re-equip |
| `Arrow keys` | Infinity Hammer (precise placement), PlantEasily (planting grid), ValheimVRMod (VR head reposition) | Three-way |
| `PageUp` / `PageDown` | Infinity Hammer (precise placement), ValheimVRMod (VR head reposition) | Two-way. Not three-way — see the SkToolbox note below |
| `Left Shift` (hold) | AzuCraftyBoxes, BowsBeforeHoes, VikingsDoSwim, Wearable Trophies, PlantEasily, Smoothbrain Farming, HarpoonExtended | Seven mods. All are context-scoped, so they rarely bite, but sprinting is `Left Shift` too |
| `Left Control` (hold) | AAA_Crafting, VikingsDoSwim, Wearable Trophies, ZenRedecorate, Smoothbrain Farming, Infinity Hammer | Six mods, plus vanilla crouch |
| `Left Alt` (hold) | PerfectPlacement (rotate Y and grid align), ZenWorldSettings, ZenRaids, RuinsMaker, Infinity Hammer | Five mods, plus vanilla dodge |
| `Left Shift` + scroll | AAA_Crafting (craft amount + 10), Infinity Hammer (scale selection) | Both fire; only one has a visible effect at a time |
| `Left Control` + scroll | AAA_Crafting (max craft amount), AdvancedTerrainModifiers (hardness) | Both fire |
| `Left Control` + middle-click on the map | Server Devcommands, Zen.ModLib | Two mods do the same thing; harmless |

**19 keys are claimed by more than one mod.**

#### Status of the three collisions named in earlier notes

| Named collision | Current status |
| --- | --- |
| `Keypad0` = PerfectPlacement *Enter Advanced Editing Mode* **and** Infinity Hammer *freeze placement* | **Resolved.** PerfectPlacement's Enter Advanced Editing Mode moved to `Keypad1` in profile 2.2.23. `Keypad0` is now Infinity Hammer alone. The collision moved with it: `Keypad1` is now shared with MagicRevamp's cycle-spell key |
| `Keypad7` = PerfectPlacement *Copy Object Rotation* **and** Infinity Hammer *undo* | **Resolved.** PerfectPlacement's Copy Object Rotation moved to `Keypad3` in profile 2.2.23. `Keypad7` is now Infinity Hammer alone. The collision moved with it: `Keypad3` is now shared with HipLantern's toggle key |
| `PageUp` / `PageDown` / arrow keys / `Home` = Infinity Hammer **and** SkToolbox **and** VHVR | **Partly wrong, and reduced.** SkToolbox is **not installed** on this profile, so it claims nothing. `PageUp`/`PageDown` and the arrow keys are a two-way collision between Infinity Hammer and VHVR. The arrow keys are a genuine three-way collision, but the third claimant is PlantEasily, not SkToolbox. `Home` is VHVR alone and has no collision at all |

### Keys that exist in a config but do nothing

These bindings are present in configuration files on the client but their mod is not installed. They
are listed so you do not go looking for behaviour that cannot happen.

| Key | Claimed by | Why it is dead |
| --- | --- | --- |
| `R`, `T`, `B` (held while interacting with a horse) | OdinHorse | Mod removed on 13 August; only its config survives |
| `Keypad6` | The VR hover menu's *Horse Wait Here* option | Points at OdinHorse, which is not installed |
| `G`, `H` (mass tame commands) | AllTameable | Not installed; config is a leftover |
| `PageUp` / `PageDown` / arrows (menu navigation) | SkToolbox | Not installed; config is a leftover on the server |

The config directory holds files for other removed mods too, and none of them have any effect:
`Azumatt.DeathPinRemoval.cfg`, `com.profmags.companions.cfg` (the VR fixes Companions HUD setting
points at this), `Azumatt.BetterWards.cfg` (superseded by WardIsLove), `kg.ItemDrawers.cfg`,
`Yggdrah.BetterRiding.cfg`, `Yggdrah.DragonRiders.cfg`, `xyz.alcan.comfortcalc.cfg` — that last one
is the exception: `comfortcalc` is ComfortTweaks' own plugin GUID, so its `F6` key is live.
A config file existing is never proof a mod is installed. Check
`active/BepInEx/plugins` for the directory.

### Unverified

- **The installed profile on the reference client is one sync behind the release.** Its generated
  `Azumatt.WardIsLove.cfg` still reads `WardHotKey = G`, and its `ForsakenPowersPlusRemastered` and
  `MagicRevamp` configs still hold the pre-move values. The shipped client-config values quoted in
  this guide (`F4`, `Keypad4`, `Keypad1`) are what a player gets after the next sync. If your ward
  key is `G`, run the launcher again.

<!-- guide:vr -->

- **`Left Shift` + `PageUp`** (AAA_Crafting Recipe UI) has not been checked against VHVR's
  `PageUp` head-reposition binding while a headset is active.

<!-- /guide:vr -->

- **Whether `Keypad8` (paste rotation) collides with anything** — nothing else on this profile binds
  it, but PerfectPlacement uses it in two different modes, and the two modes were not tested
  together.
- **How OdinArchitect's pieces are categorised** in the hammer menu. Its config declares no build
  category on this server, so its tab name could not be established from a file.
- **The exact set of vanilla bindings each player is running.** Vanilla keys are stored outside the
  synced profile, so the vanilla rows in the table above are documented defaults rather than
  observed values.
