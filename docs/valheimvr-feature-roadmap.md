# Proposed ValheimVR feature roadmap

> Status: discussion document. It makes no product commitment and does not authorize implementation.

## Purpose

This is a ranked set of possible ValheimVR improvements for the VR client experience. The aim is to increase comfort, presence, and accessibility without changing Valheim's authoritative gameplay, server protocol, world data, or dedicated-server deployment.

The Flat and VR profiles remain separate client experiences. A Flat player keeps ValheimVR's required multiplayer compatibility layer but must not initialize a VR runtime. VR-only behavior must remain local to the VR client.

## Design rules

- Prefer existing vanilla actions, validation, cooldowns, inventory checks, and network messages over replacement gameplay systems.
- Keep new behavior local unless the game already replicates the resulting state.
- Put every VR-only feature behind a configuration option and preserve a conventional controller/keyboard path.
- Do not require a dedicated-server mod, world migration, custom assets, or a new multiplayer protocol for an initial feature.
- Avoid direct SteamVR API use outside initialization and runtime-adapter boundaries.
- Treat locomotion comfort and accessibility as first-class requirements, not polish.
- Ship a small vertical slice, test it with real movement/combat/building, then expand.

## Tier 1 — comfort, accessibility, and feedback

These are the best first releases: high player value, bounded scope, and limited interaction with Valheim authority.

### 1. Dynamic comfort vignette

Apply a stereo-safe peripheral vignette while artificial locomotion, snap/smooth turning, dodging, falling, swimming, flying, or camera-relative motion exceeds a configurable threshold.

**Controls**

- Independent toggles for movement and turning.
- Minimum and maximum aperture.
- Activation/release speed and smoothing.
- Per-locomotion-mode overrides.
- Optional static nose/reference marker.

**Implementation shape**

Use a VR-local compositor/shader path rather than the current full-screen fade behavior. The effect should read current movement and camera motion only; it must not affect player movement, damage, animation, or replication.

**Acceptance signal**

A player can run, sail, dodge, teleport, die, and load with no one-frame full-screen flash or non-stereo artifact. Disabling the option restores current visuals exactly.

### 2. Seated, standing, and accessibility calibration profiles

Persist named calibration presets rather than one implicit room setup.

**Profiles**

- Standing room-scale.
- Standing limited-space.
- Seated.
- One-handed accessibility.
- Custom.

**Calibration inputs**

- HMD floor/seat height.
- Comfortable arm reach.
- Player scale and floor offset.
- Dominant hand and controller assignment.
- Room-scale versus stick locomotion preference.
- Camera/recenter offset.

**Implementation shape**

Reuse existing player-height, room-scale, handedness, camera-offset, and controller settings. A profile is a coherent settings snapshot plus a guided recenter flow; it must not be a parallel configuration system.

**Acceptance signal**

Switching a preset changes only profile-owned settings, survives restart, and can be reversed in one action. A seated player can use UI, bows, building, and vehicles without standing-only assumptions.

### 3. Directional controller haptics

Make feedback distinguishable by event and direction instead of a single generic pulse.

**Candidate patterns**

- Left/right-side hit: stronger pulse in the corresponding controller.
- Frontal heavy hit: both controllers, weighted toward attack side.
- Rear hit: short alternating pulse or both controllers.
- Parry/block: sharp pulse in the blocking hand.
- Successful weapon contact: weapon-hand pulse.
- Poison, fire, frost, and drowning: low-frequency contextual patterns.

**Implementation shape**

Centralize patterns behind a small controller-haptics service. Existing systems should submit semantic events, not invoke SteamVR haptics directly. Deduplicate repeated collision/UI hover events and keep all patterns configuration-controlled.

**Acceptance signal**

Feedback is consistent across combat, shields, weapons, and environmental damage; sustained effects do not flood the controller with pulses.

## Tier 2 — local physical interactions

These features should call existing game actions rather than inventing parallel inventory, crafting, or network behavior.

### 4. Grip-to-pickup world items

A nearby dropped item can be selected by hand proximity and grabbed into the player's inventory.

**Interaction**

1. Show a local visual proxy/highlight for the selected valid drop.
2. Confirm grip or a configured direct-grab gesture.
3. Invoke Valheim's normal item pickup path.
4. Play item-specific sound/haptic feedback.
5. Remove the local selection only after the vanilla result is known.

**Boundaries**

- Limit the first version to portable dropped items.
- Do not replace chest, crafting-station, building-piece, or equipped-item interaction.
- Do not bypass weight, inventory-space, ownership, or multiplayer pickup validation.

### 5. Physical eating, drinking, and potions

Provide a small wrist/radial proxy for consumables, then use the normal item-use path.

**Interaction**

- Select food, mead, or potion from a local UI proxy.
- Bring it close to the mouth or hold the action briefly.
- Call vanilla `UseItem` and let Valheim decide whether use is legal.
- Add local sound and haptic feedback only after success.

**Risk**

The item proxy must not create a second inventory, consume items early, or duplicate input. Inventory state stays authoritative in Valheim.

### 6. Physical workstation controls

Add hand-proximity affordances for common vanilla interactions:

- levers and switches;
- chest lids and containers;
- smelter/furnace fuel and ore slots;
- cooking stations;
- item stands;
- fireplace and light controls.

**Approach**

Detect a narrow interaction volume, show an affordance, and dispatch the existing `Interact` or `UseItem` action. Expand one object family at a time; prefab-specific behavior is preferable to a generic gesture system that guesses incorrectly.

## Tier 3 — embodiment and spatial tools

These have greater payoff but need careful interaction design and more compatibility testing.

### 7. Body holsters and direct inventory

Configurable zones such as left/right hip, back/shoulder, chest, and wrist can expose equipped items or quick categories.

**First version**

Treat holsters as shortcuts to existing inventory slots. Releasing an item into a valid zone selects/equips it through vanilla inventory logic. Do not model arbitrary loose storage in body space.

**Risks**

Backpack and equipment-slot mods may change inventory semantics. Validate against the existing Backpacks integration before expanding beyond direct slot shortcuts.

### 8. Two-hand building-piece manipulation

Improve placement-ghost control without replacing Valheim placement rules.

**Interaction**

- One hand: translate the ghost.
- Two hands: rotate around the midpoint.
- Hand separation: optional bounded scaling only where the game allows it.
- Release: return control to vanilla placement snapping, resource use, and validation.

**Boundary**

Never free-place an object outside Valheim's placement checks. The VR layer controls intent and preview only.

### 9. Physical wrist map

Allow the minimap to expand into world space and be manipulated locally.

**Interaction**

- Raise wrist to open a compact map.
- Two hands pan and zoom.
- Touch to inspect markers.
- Long press to place a normal Valheim pin.
- Fold/return to close.

**Boundary**

Reuse existing minimap components and pin APIs. No new map persistence format or network protocol.

### 10. Gesture-driven magic

Optional, conservative gestures for existing staff attacks and forsaken abilities.

**First version**

A gesture chooses between existing primary/secondary action paths only after a deliberate controller action. Vanilla attack validation, resource consumption, cooldowns, projectiles, and effects remain authoritative.

**Risk**

Avoid hard-coded item names. Disable by default until false-positive rates and accessibility concerns are understood.

### 11. Adaptive hand poses and contact IK

Improve presence by aligning hands around weapon grips and nearby interactable geometry.

**Scope**

- Weapon and shield grip regions.
- Doors, ladders, levers, tables, and chests.
- Lightweight local hand pose blending.
- Optional contact-aware positioning for hands, waist, and feet.

**Risk**

Do not let local IK change hitboxes, networked transforms, or item placement. Start with visual-only poses.

## Tier 4 — platform and advanced features

These are strategic projects. They should not be mixed with gameplay-feature releases.

### 12. OpenXR runtime backend

Abstract tracking, controller poses, actions, and haptics behind a runtime-neutral interface, then implement OpenXR alongside the current SteamVR backend.

**Value**

- Less direct SteamVR dependency.
- Better compatibility with non-SteamVR runtimes.
- Cleaner input/tracking architecture.

**Risk**

SteamVR actions and haptic calls are currently widespread. This needs an initialization-first migration, a compatibility matrix, and explicit testing for controllers, trackers, bindings, and fallback behavior.

### 13. Ladder climbing and mantling

Support constrained hand-over-hand climbing on intentional ladder volumes, followed by a safe top-of-ladder transition.

**Phased scope**

1. Vanilla ladders only.
2. Hand-over-hand local motion while gripping a ladder volume.
3. Stamina and release/fall behavior.
4. Top-of-ladder transition.
5. Mounting and modded ladders only after the base behavior is stable.

**Risk**

Collision, multiplayer authority, moving structures, and motion comfort make arbitrary surface climbing out of scope for an initial release.

### 14. Mixed-reality and spectator camera tools

Provide stable third-person/spectator framing for streaming or local recording.

**Possible controls**

- Player-follow camera.
- Camera attachment to ships or moving bases.
- Optional free-camera framing.
- Stable composition presets.
- Local-only capture controls.

**Boundary**

This is a spectator/rendering feature. It should not grant gameplay camera advantages, alter network state, or become a prerequisite for normal VR play.

## Suggested delivery order

| Release | Focus | Features |
|---|---|---|
| A | Comfort and accessibility | Dynamic vignette, haptic service, calibration presets |
| B | Everyday physical interaction | Grip-to-pickup, consumables, one workstation family |
| C | Spatial interaction | Holsters, wrist map, two-hand building preview |
| D | Advanced interaction | Gesture magic, visual hand poses, ladder prototype |
| E | Runtime strategy | OpenXR investigation as a separate compatibility project |

## Feature acceptance checklist

Before enabling any feature by default:

1. It has a player-visible configuration entry and safe default.
2. It works on the current supported VR runtime and does not initialize VR for Flat profiles.
3. It preserves vanilla action validation and does not require server/world changes.
4. It is tested with a fresh profile, an upgraded profile, and a multiplayer client.
5. It does not leave stale objects, Harmony patches, or altered Steam files after disable, failure, or profile switch.
6. It has a focused smoke scenario covering the new interaction and its failure path.
