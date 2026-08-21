using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Client-side VR corrections for this profile. Shipped only in VR releases, so
    // nothing here affects Flat clients or the dedicated server.
    //
    // Every fix below addresses a defect established by measurement or by reading the
    // upstream source, not by guesswork:
    //
    //  1. VRGUI.updateUiPanelScaleAndPosition runs from OnRenderObject, which Unity
    //     calls once per rendering camera. Confirmed identical in VHVR v0.9.20,
    //     v0.9.21 and master, with no frame guard anywhere in the tree. Two unit errors
    //     compound across those calls: Vector3.MoveTowards is handed
    //     Time.unscaledDeltaTime as maxDistanceDelta (metres given seconds), and
    //     GuiRecenterSpeed * Deg2Rad * dt is handed to Slerp/Lerp as a 0..1 fraction.
    //     At 90 fps with one call per frame this looks sane; at 20 fps with ten calls
    //     the probe measured the panel moving 0.77 m and 14.8 degrees WITHIN one frame,
    //     so each eye renders the HUD somewhere different. Unreported upstream, so
    //     there is no version to upgrade to.
    //
    //  2. VHVR only converts canvases whose name is on a hardcoded list, and
    //     ensureGuiCanvas scans once then short-circuits forever. A mod that builds its
    //     own root Canvas therefore renders in screen space while the rest of the UI is
    //     a world-space quad. There is no sanctioned registration API - VRGUI is an
    //     internal type with private fields - so this uses the same reflection pattern
    //     as geekstreet's EpicLootVRFix and XPortalVRFix.
    //
    //  3. The AzuEPI quick-slot bar is redundant in VR, where inventory is handled with
    //     controllers. Its visibility setting is [Synced with Server], so it cannot be
    //     turned off for VR alone through configuration; hiding it client-side is the
    //     only way to keep Flat players' bars intact.
    [BepInPlugin(GUID, "Neuralyze VR Fixes", Version)]
    public class NeuralyzeVRFixesPlugin : BaseUnityPlugin
    {
        public const string GUID = "neuralyze.vrfixes";
        public const string Version = "1.50.0";
        internal const string Tag = "[NeuralyzeVRFixes] ";

        internal static ManualLogSource Log;
        internal static ConfigEntry<bool> FramePanelGuard;
        internal static ConfigEntry<bool> HideQuickSlotBar;
        internal static ConfigEntry<string> AdoptCanvases;
        internal static ConfigEntry<float> AdoptPollSeconds;
        internal static ConfigEntry<string> CompanionHudPlacement;
        internal static ConfigEntry<float> RenderScale;
        internal static ConfigEntry<bool> SuppressKeyHints;
        internal static ConfigEntry<bool> SweepUILayer;
        internal static ConfigEntry<bool> HideMinimap;
        internal static ConfigEntry<bool> HideHotbarRoot;
        private static bool _hotbarsDone;
        internal static ConfigEntry<bool> LogHoverText;
        internal static ConfigEntry<bool> LogJumpInput;
        internal static ConfigEntry<bool> BridgeInput;
        internal static ConfigEntry<bool> DirectActions;
        internal static ConfigEntry<float> LaserReach;
        internal static ConfigEntry<float> AttackReach;
        internal static ConfigEntry<bool> GripJumpDodges;
        internal static ConfigEntry<bool> BuildMenuTapOpens;
        internal static ConfigEntry<bool> GripRemovesPiece;
        internal static ConfigEntry<bool> FlyControls;
        internal static ConfigEntry<string> MiscMenuHand;
        internal static ConfigEntry<bool> ProfileHooks;
        internal static ConfigEntry<string> HoverModifier;
        internal static ConfigEntry<string> HorseSteering;
        internal static ConfigEntry<bool> HoverMenuEnabled;
        internal static ConfigEntry<string> HoverActions;
        internal static ConfigEntry<bool> TriggerAttacks;
        internal static ConfigEntry<bool> DirectCrouch;
        internal static ConfigEntry<bool> Profile;
        internal static ConfigEntry<bool> ProfilePlugins;
        internal static ConfigEntry<bool> ProfileInventory;
        internal static ConfigEntry<bool> HideFastLinkTitle;
        internal static ConfigEntry<bool> ProfileGameMethods;
        internal static ConfigEntry<bool> SweepRenderScale;
        internal static ConfigEntry<bool> MeasureCombatLatency;
        internal static ConfigEntry<bool> RestorePhysicsRate;
        internal static ConfigEntry<bool> MoveWhilePanelOpen;
        internal static ConfigEntry<bool> HideAdminEntries;
        internal static ConfigEntry<float> SteamVrScale;
        internal static ConfigEntry<bool> MiscMenuEnabled;
        internal static ConfigEntry<string> MiscMenuActions;
        internal static ConfigEntry<string> AnchorGrip;
        internal static ConfigEntry<string> AnchorKey;
        internal static ConfigEntry<bool> WatchHelm;

        private void Awake()
        {
            Log = Logger;
            FramePanelGuard = Config.Bind("1 - GUI panel", "SinglePanelUpdatePerFrame", true,
                "Run VHVR's GUI panel placement once per frame instead of once per rendering camera. " +
                "Without this the left and right eye can be given different panel positions, which reads as flicker.");
            HideQuickSlotBar = Config.Bind("2 - Inventory", "HideQuickSlotBar", true,
                "Hide the AzuEPI quick-slot bar. Inventory is handled with controllers in VR, and the mod's own " +
                "visibility setting is synced from the server so it cannot be disabled for VR alone.");
            AdoptCanvases = Config.Bind("3 - Canvases", "AdoptCanvasNames",
                "HC_StarterCompanionPanel,HC_CompanionInteractPanel,HC_CompanionRadialMenu,HC_RadialCanvas,RadialRoot," +
                "HC_CompanionHudPanelHost,HC_CompanionHudPanel,HC_ConfigPanel,HC_HomeZonePanel,HC_FarmZoneHud," +
                "CropPickerCanvas,CropPickerRoot,ConsolePanel.Canvas,TutorialOverlayCanvas,KeyBindingOverlay",
                "Comma-separated Canvas GameObject names to convert to VHVR's world-space GUI. VHVR only converts a " +
                "hardcoded list of names and scans once at startup, so mod canvases created later render in screen " +
                "space. Names are matched exactly - an earlier version of this list used the panel CLASS names " +
                "(StarterCompanionPanel, ConfigPanel, HomeZonePanel) which never match, because Offline Companions " +
                "prefixes every GameObject with HC_.");
            AdoptPollSeconds = Config.Bind("3 - Canvases", "AdoptPollSeconds", 0.5f,
                "How often to re-scan for unconverted canvases. Companion panels are built on demand and the radial " +
                "is destroyed on close, so a one-shot scan catches none of them; a name stays eligible for the whole " +
                "session rather than being marked done after its first adoption.");
            CompanionHudPlacement = Config.Bind("7 - Companions", "CompanionHudPlacement", "RightWrist",
                "Where the Offline Companions status panel lives: RightWrist, LeftWrist, or Off. The mod anchors it " +
                "beside the flat-HUD minimap, which VHVR moves to a wrist canvas, so converting it in place leaves " +
                "it floating where the flat minimap used to be. Parenting it into VHVR's wrist canvas is how VHVR " +
                "places its own panels. Off leaves the panel to the canvas adopter instead.");

            SuppressKeyHints = Config.Bind("5 - Hover text", "SuppressKeyboardKeyHints", true,
                "Remove keyboard prompts such as \"[E] Pick up\" from the hover text. Those hints name keys that do " +
                "not exist in VR, where the same actions are SteamVR button bindings. VHVR repositions hover text but " +
                "has no option to change its content: all 132 of its config keys were checked and none applies.");
            SweepUILayer = Config.Bind("3 - Canvases", "SweepUILayerOnAdoptedCanvases", true,
                "Move every canvas VHVR has adopted onto the UI layer. VHVR assigns worldCamera but never the layer, " +
                "while its GUI camera renders only layer UI, so any adopted canvas left on another layer is invisible " +
                "in VR. Several installed mods never set a layer at all.");
            HideHotbarRoot = Config.Bind("2 - Inventory", "DeactivateHotbarRoot", true,
                "Deactivate the HotkeyBar GameObjects, including AzuEPI_QuickAccessBar. The measured flicker source " +
                "is AzuEPI's own prefix on HotkeyBar.Update, which clears and destroys its elements every frame " +
                "(7-15 clears/sec observed). Deactivating the root stops Update entirely, so there is nothing to " +
                "rebuild and nothing to draw. Hotkeys keep working: they are input-driven, not UI-driven. Applied " +
                "only once a world is fully loaded. Set false first if a session fails to load.");
            HideMinimap = Config.Bind("6 - Minimap", "HideCornerMinimap", true,
                "Deactivate the flat-HUD minimap that VHVR abandons when it moves the map to a wrist panel. VHVR " +
                "clones Minimap.m_smallRoot and repoints m_smallRoot at the clone, so m_smallRoot IS the wrist map " +
                "and must not be touched; this targets VHVR's own Original reference instead, and only once a Clone " +
                "exists. With MinimapPanelPlacement=Legacy no clone is made and this does nothing.");
            LogHoverText = Config.Bind("5 - Hover text", "LogDistinctHoverText", true,
                "Log each distinct raw hover string before stripping, up to 20. This exists because the strip is only " +
                "as good as its assumption about the text format; the log states the real format instead.");
            DirectActions = Config.Bind("8 - Input bridge", "InvokeGameplayDirectly", true,
                "Call the game's own methods when a button is pressed, instead of routing through ZInput. " +
                "Measured: SteamVR delivers every press correctly, and raising it through VHVR's EmulateButtonDown " +
                "hook did fire (9 bridged 'Jump' in one session) yet nothing happened - because that hook is " +
                "single-consumption and whichever of the ~100 installed mods polls ZInput first eats the press. " +
                "This path has no such race: Jump calls Character.Jump(), crouch calls SetCrouch(), and the right " +
                "trigger calls Player.Interact() on the hover object VHVR's right laser pointer already resolves.");
            BridgeInput = Config.Bind("8 - Input bridge", "BridgeSteamVRToZInput", false,
                "Deliver the button presses VHVR drops. Measured: SteamVR reports the A button, X button, " +
                "trigger and stick clicks arriving correctly (correct oculus_touch origins, activeBinding true), " +
                "yet ZInput never sees them, so the game never acts. VHVR discards them in VRControls.GetButtonDown: " +
                "Jump is gated behind canJump(), and Crouch, Run, Hide and Block sit in a hardcoded ignore list. " +
                "This reads the SteamVR actions directly and calls VHVR's own public EmulateButtonDown hook, whose " +
                "prefix runs before every gate. Set false to restore stock behaviour.");
            LaserReach = Config.Bind("8 - Input bridge", "RightLaserReachMetres", 8.0f,
                new ConfigDescription(
                    "How far the right laser pointer can interact. VHVR resolves the pointer correctly but then " +
                    "discards the result unless the target is within vanilla's interact distance of your EYE " +
                    "(HandBasedInteractionPatches line 201), so a laser that visibly reaches 50m only works inside " +
                    "about 2m. This raycasts from the pointer itself and interacts with what it hits, so the laser " +
                    "works at the distance it appears to.",
                    new AcceptableValueRange<float>(2f, 30f)));
            DirectCrouch = Config.Bind("8 - Input bridge", "InvokeCrouchDirectly", false,
                "Leave this off. VHVR already implements crouch from valheim_ToggleCrouch in " +
                "ControlPatches.handleControllerOnlySneak, and it also maintains an _isJoystickSneaking flag that " +
                "stops its roomscale handler from immediately toggling crouch back off. Flipping m_crouchToggled " +
                "directly bypasses that bookkeeping, which is why a direct crouch held while walking but collapsed " +
                "instantly while standing still. Crouch is handled by SneakInput=ControllerOnly instead.");
            GripJumpDodges = Config.Bind("8 - Input bridge", "GripPlusJumpDodges", false,
                "OFF BY DEFAULT because it broke jump. Grip rests under light pressure on Touch controllers, so valheim_Grab.GetState reads true constantly and every jump silently became a dodge. Only enable if you can hold grips genuinely open. Hold either grip and press Jump to dodge-roll. VHVR has no dodge button at all: " +
                "/actions/Valheim/in/Dodge is declared in the action manifest but bound to no physical input, and " +
                "dodging is only reachable as a gesture (GesturedDodgeRoll) that needs you crouched, your head " +
                "dropping faster than 1.5 m/s and your hands over 2 m/s - all measured from frame-to-frame velocity, " +
                "so it is as frame-rate sensitive as swinging a weapon. This calls Player.Dodge directly instead. " +
                "Direction follows the movement stick, or rolls backwards if the stick is centred.");
            TriggerAttacks = Config.Bind("8 - Input bridge", "TriggerAttacksWhenNothingTargeted", true,
                "Swing when the right trigger is pulled and the laser has no target. VHVR only starts attacks from " +
                "swing momentum, and measured hand speed is 0.00 m/s on both hands in every sample - the physics " +
                "estimator supplies nothing, so no real swing can pass hasMomentum and no attack is reachable at all. " +
                "Pointing at something still interacts; only an untargeted pull attacks.");
            HoverMenuEnabled = Config.Bind("11 - Hover actions", "Enabled", true,
                "Contextual actions on whatever the laser is pointing at. 51 of the 160 mod key bindings on this " +
                "install are target-dependent - \"hold a key while interacting with X\", or a global hotkey that " +
                "guesses which container you meant - and neither idiom survives VR. Point at the thing, hold the " +
                "grip on the hand named by Modifier - the right, pointing hand by default - push the right stick " +
                "up or down to move through the offered actions, and release the grip to run the highlighted one. " +
                "That hand's trigger keeps its normal meaning, because nothing here suppresses it: a hold-to-open " +
                "scheme on a trigger would fire mount/open/attack first. The stick was rejected as the opening " +
                "gesture, being walk and turn, but it has no other job once the list is up; hand-motion gestures " +
                "were rejected because the physics estimator reports 0.00 m/s on this install.");
            // The ward default was key:F5 until 2026-08-20. F5 is Valheim's console, which the
            // launcher keeps live by passing -console, so a first run with no shipped cfg pointed
            // the ward button at the console. Keypad4 is what the release now ships (see the
            // 2026-08-17 F4 collision with IdentityCrisis, recorded in neuralyze.vrfixes.cfg);
            // the default has to agree with it, because a code default only applies on that first
            // run and BepInEx then persists whatever it wrote.
            HoverActions = Config.Bind("11 - Hover actions", "Actions",
                "horse: Wait Here=hold:Keypad6 | Saddlebags=hold:B | Remove Armour=hold:R"
                + " ; container: Quick Stack=key:P | Restock=key:L | Sort=key:O | Store All=key:Period"
                + " ; ward: Toggle Permission=key:Keypad4"
                + " ; fireplace: Infinite Fuel=hold:LeftAlt"
                + " ; ship: Anchor=key:LeftShift+F"
                + " ; piece: Repair Area=key:LeftShift+W | Add Wear=key:LeftAlt+W",
                "One group per target kind, separated by ';'. Options within a group are separated by '|' and read " +
                "Label=kind:value. 'key' pulses the key; 'hold' holds it and interacts with the pointed-at object " +
                "in the same frame, which is what mods mean by \"held while interacting\". Target kinds are " +
                "resolved by component: horse (Tameable named horse), container, ward (PrivateArea), fireplace, " +
                "ship, piece (WearNTear). Adding a target is a config edit, not a code change.");

            HorseSteering = Config.Bind("12 - Mounts", "HorseSteering", "Stick",
                new ConfigDescription(
                    "Stick: the left stick drives the horse the way a gamepad would - push to go, left and right " +
                    "to turn - and your head is free to look around. Look: the horse follows where you look, which " +
                    "is what a desktop player gets from the mouse. Speed is the same stick either way.",
                    new AcceptableValueList<string>(new string[] { "Stick", "Look" })));

            // Was declared with a LeftGrip default and then never read - HoverMenu hardcoded the
            // right grip - so the config file has been announcing a hand the plugin does not use.
            // The default now names the hand that actually ships, and the value is read.
            HoverModifier = Config.Bind("11 - Hover actions", "Modifier", "RightGrip",
                new ConfigDescription(
                    "Which control opens the contextual menu on whatever you point at: RightGrip, LeftGrip or " +
                    "LeftTrigger. RightGrip is the default because the hand that points is the hand that " +
                    "chooses, and the grip fires nothing on press. The other two are for a player who points " +
                    "with the left hand. A trigger is a poor choice either way - it is the game's own use " +
                    "button, so pointing at a horse and pulling it mounts you, which then makes the horse " +
                    "unpointable and its menu unreachable - and there is no RightTrigger option for that " +
                    "reason. The highlight is moved with the right stick regardless of this setting.",
                    new AcceptableValueList<string>(new string[] { "RightGrip", "LeftGrip", "LeftTrigger" })));

            ProfileHooks = Config.Bind("9 - Diagnostics", "ProfileOurHooks", false,
                "Off by default for players: the timer wraps every hook, and the panel hook alone runs 400+ times a frame, so the measurement costs more than most of what it measures. Turn it on to diagnose a frame rate complaint. Logs what THIS plugin's hooks cost, in milliseconds per frame, every five seconds. Three frame " +
                "rate regressions this session were diagnosed by reading code and guessing; this measures instead. " +
                "A frame at 72Hz is 13.9ms, so any hook approaching 1ms/frame is ours to fix - and a total well " +
                "under that while the game still stutters means the cause is elsewhere, which is equally useful.");

            MiscMenuHand = Config.Bind("10 - Misc controls", "MenuHand", "Right",
                new ConfigDescription(
                    "Which wrist carries the Misc entry. VHVR defines reorderElements on the shared QuickAbstract base, " +
                    "so one patch fires for both strips and the entry appeared on both wrists. This matches the " +
                    "physical hand from SteamVR's own Hand.handType, because the subclass name proved wrong - so it stays put even if " +
                    "QuickActionOnLeftHand swaps which strip holds the quick bar.",
                    new AcceptableValueList<string>(new string[] { "Right", "Left", "Both" })));
            FlyControls = Config.Bind("8 - Input bridge", "FlyAscendDescend", true,
                "Ascend and descend while debug-flying. Character.UpdateMotion reads ascent from " +
                "ZInput.GetButton(\"Jump\")/(\"JoyJump\") and descent from ZInput.GetButtonPressedTimer(\"JoyCrouch\"). " +
                "VHVR only patches GetButton, GetButtonDown and GetButtonUp, so descent is unreachable in VR by " +
                "construction - nothing feeds the timer and it stays zero however long the stick is held. Both reads " +
                "are answered here from the SteamVR actions, only while actually debug-flying, so ordinary jumping " +
                "and crouching are untouched.");
            GripRemovesPiece = Config.Bind("8 - Input bridge", "GripPlusARemovesPiece", true,
                "Removes the piece you are pointing at with the right grip held and A pressed, which is VHVR's own " +
                "arrangement: canRemovePiece() requires place mode, the right grip, and a live BuildingManager, and " +
                "canJump() yields to it because \"removing piece takes higher priority than jump\". That arbitration " +
                "lives in VHVR's ZInput path; this plugin invokes jump directly because that path does not deliver " +
                "it, so without this the press is consumed as a jump and nothing is ever removed - measured as three " +
                "jumps and no removal. Calls Player.RemovePiece directly, and suppresses the jump for that press.");
            BuildMenuTapOpens = Config.Bind("8 - Input bridge", "TapOpensBuildMenu", true,
                "Opens the build menu, which is otherwise unreachable in VR. Valheim opens piece selection from " +
                "Player.UpdateBuildGuiInput, which reads the vanilla button \"BuildMenu\"; VHVR maps that name to " +
                "laserPointers_RightClick (B) and injects VR input by OR-ing GetStateDown into ZInput.GetButtonDown. " +
                "Measured with a hammer equipped and every VHVR precondition true - place mode, active pointer, " +
                "active LaserPointers set, the action firing - ZInput.GetButtonDown(\"BuildMenu\") was false on every " +
                "frame: the level arrives, the edge never does, so the game is never told the button was pressed. " +
                "This reads the level, derives the edge, and calls Hud.TogglePieceSelection directly, exactly as " +
                "dodge calls Player.Dodge. Toggled on release of a press under 0.3s, matching VHVR's own timer, so " +
                "holding B still opens the quick-select radial and grip+B still forces it.");
            Profile = Config.Bind("9 - Profiling", "FrameAndVrReport", true,
                "Report frame pacing (mean/p50/p95/p99), per-eye VR render resolution read from OpenVR rather than " +
                "XRSettings, active camera census, and OpenVR compositor timing including reprojected and dropped " +
                "frames. Cheap, and the compositor numbers are the only authoritative statement of GPU cost.");
            ProfilePlugins = Config.Bind("9 - Profiling", "ProfilePluginUpdateCost", false,
                "Measurement mode: Harmony-wrap Update/LateUpdate/FixedUpdate on every type in every BepInEx plugin " +
                "assembly and report per-mod ms/frame. Instrumenting hundreds of methods costs measurable time " +
                "itself, and it attributes only MonoBehaviour messages - not a mod's patches on game methods, its " +
                "coroutines, or its GC pressure - so results are lower bounds. Turn on to measure, off to play.");
            SweepRenderScale = Config.Bind("9 - Profiling", "SweepRenderScaleOnce", false,
                "Step renderViewportScale 1.0 / 0.85 / 0.7 / 0.5 for 20s each, then restore, reporting frame stats " +
                "per step. Frame time that falls with pixel count is GPU-bound; frame time that does not move is " +
                "CPU-bound. This single answer decides whether cutting mods or cutting resolution is the right lever.");
            AttackReach = Config.Bind("8 - Input bridge", "AttackReachMetres", 3.0f,
                new ConfigDescription(
                    "Reach of the trigger attack. VHVR replaces Attack.Start entirely with its own prefix " +
                    "(CollisionPatches.cs:25-130) which reads StaticObjects.lastHitCollider/lastHitPoint/lastHitDir " +
                    "and, if lastHitCollider is null, returns having already set __result=true - so vanilla never " +
                    "runs, no animation event fires, and no damage happens. That is exactly why our earlier " +
                    "StartAttack calls reported success 13 times with no effect. We now raycast a target and publish " +
                    "those three statics first, so VHVR's own damage path does the work.",
                    new AcceptableValueRange<float>(1.5f, 8f)));
            MiscMenuEnabled = Config.Bind("10 - Misc controls", "EnableMiscRadialMenu", true,
                "Add a 'Misc' entry to VHVR's wrist radial menu that pages through mod actions VR cannot otherwise " +
                "reach. VHVR permanently ignores any ZInput name it has no binding for (VRControls.cs:521), and mods " +
                "bound to keyboard shortcuts have no VR path at all - 22 unmapped actions were observed on this " +
                "install. Entries are injected into VHVR's own radial via its public useAsQuickAction, so there is no " +
                "second UI to maintain.");
            MiscMenuActions = Config.Bind("10 - Misc controls", "Actions",
                "Emotes = zinput:OpenEmote, Map Zoom In = zinput:MapZoomIn, Map Zoom Out = zinput:MapZoomOut, Chat = zinput:Chat, Toggle HUD = zinput:JoyToggleHUD, Forsaken Power = zinput:GP, Interact Alt (Zen) = zinput:Zen_ModLib_InteractAlt, Auto Pickup = zinput:JoyAutoPickup",
                "Comma-separated list of Label = kind:value.  kind is 'zinput' for a ZInput action name or 'key' for " +
                "a keyboard shortcut (chords with +).  Example:\n" +
                "  Interact Alt = zinput:Zen_ModLib_InteractAlt, Quick Stack = key:LeftControl+V, Emotes = zinput:OpenEmote\n" +
                "Adding a mod means adding a line here, not changing code. Run tools/vr_impact_scan.py against the " +
                "mod package to recover the exact ZInput names and KeyboardShortcut defaults to put in this list.");
            MeasureCombatLatency = Config.Bind("9 - Profiling", "MeasureCombatLatency", true,
                "Time the gap between hand speed crossing the attack threshold and the damage path actually running, " +
                "reported in milliseconds AND in frames. Frames matter: one frame at 25 FPS is 40 ms that no combat " +
                "tuning can recover, so it separates frame-rate cost from code cost. Also counts swings that never " +
                "produced an attack, which is a different complaint from everything being late. Cheap - two postfixes " +
                "and one velocity read per frame.");
            RestorePhysicsRate = Config.Bind("9 - Profiling", "RestoreVanillaPhysicsRate", false,
                "OFF by default - enable for one session and compare. SteamVR overwrites Time.fixedDeltaTime with " +
                "1/hmd_DisplayFrequency every frame (lockPhysicsUpdateRateToRenderFrequency in its settings asset), so " +
                "physics runs at 72-90Hz instead of Valheim's own 50Hz. At 20-28 FPS that is 3-5 complete FixedUpdate " +
                "cycles per rendered frame - 1.4-1.8x the CPU the game was tuned for - and the extra ones cannot " +
                "improve hit detection because the weapon collider is only repositioned during rendering. Restoring " +
                "0.02s frees that CPU for frame rate, which is the multiplier on every remaining latency term. " +
                "Known trade-off: PhysicsEstimator's window is 8 STEPS, so it widens from ~80ms to 160ms, which can " +
                "worsen primary-vs-secondary attack selection. Measure before adopting.");
            HideAdminEntries = Config.Bind("10 - Misc controls", "HideAdminEntriesForNonAdmins", false,
                "Hide the admin wrist buttons unless the local player is an admin. OFF by default because the " +
                "detection is not trustworthy: a measured session logged ZNet.LocalPlayerIsAdminOrHost()=False " +
                "for a player who IS in the server's adminlist, which hid the console from its own admin. The " +
                "buttons only ATTEMPT a command and the server refuses a non-admin regardless, so showing them " +
                "costs nothing. Turn this on once the log shows admin=True reliably.");
            SteamVrScale = Config.Bind("9 - Profiling", "SteamVrSceneResolutionScale", 0f,
                "SteamVR scene render scale. 0 leaves SteamVR alone (default). This is the ONLY resolution lever " +
                "that works on this pipeline - XRSettings.renderViewportScale reads 0 because Valheim renders " +
                "through OpenVR, not Unity's XR display subsystem, so the older RenderScale setting was inert. " +
                "Measured: the per-eye target is 1933x2066, i.e. 8.0 MPix per frame at 15-20 ms of GPU. Pixels " +
                "dominate GPU cost, so 0.85 removes about 28% of them. Try 0.85, then 0.75, and compare " +
                "'PERF frame' and 'gpuMs' between sessions.");
            MoveWhilePanelOpen = Config.Bind("8 - Input bridge", "MoveWhileTextPanelOpen", true,
                "Keep walking and turning while the console, chat or a text input is open. Player.TakeInput() " +
                "blocks all input when any of those is visible, which makes sense on flatscreen - the keyboard " +
                "is typing - but in VR typing is on the overlay keyboard and the button that closes the panel is " +
                "on your wrist, so a frozen player cannot walk to reach it. Inventory, the main menu and stores " +
                "still block input.");
            LogJumpInput = Config.Bind("7 - Diagnostics", "LogJumpInput", true,
                "Log every frame ZInput reports Jump pressed, with whether a Player exists and can act. Distinguishes " +
                "'the button never reaches the game' from 'the game refused the jump'.");
            RenderScale = Config.Bind("4 - Performance", "RenderViewportScale", 1.0f,
                new ConfigDescription(
                    "Fraction of the eye texture actually rendered. 1.0 changes nothing. Lowering it is the cheapest " +
                    "way to cut GPU cost in VR: frame cost scales with pixels, and it is instantly reversible. Only " +
                    "worth changing if the eye resolution reported at startup shows the client is pixel-bound.",
                    new AcceptableValueRange<float>(0.5f, 1.0f)));

            ProfileInventory = Config.Bind("9 - Profiling", "ProfileInventoryPanel", false,
                "Time the game's own inventory methods and name every mod patching each one. Measured on this " +
                "install: the panel costs 18.6ms per open frame across sixteen methods, with ten mods on " +
                "InventoryGui.Update alone. Off for players - it wraps methods that run every frame the panel is " +
                "open - and on to find out which mod owns that time.");

            ProfileGameMethods = Config.Bind("9 - Profiling", "ProfileGameMethods", false,
                "Time the game's own hot methods - the player tick, world streaming, the HUD, the map - and name " +
                "every mod patched onto each. Measured here: 12fps with 28-34ms of GPU and about 40ms per frame of " +
                "processor time that mod update loops did not account for, which is where 111 mods' patches on game " +
                "methods live. Off for players; on to find out what owns a frame.");

            HideFastLinkTitle = Config.Bind("5 - Hover text", "HideFastLinkTitle", true,
                "Hide the FastLink panel's title. FastLink scales as one piece, so enlarging the server names to " +
                "read them in VR enlarges a title that was already the largest thing on the panel, and it covers " +
                "the character on the creation screen. The title is also the panel's drag handle, so with this on " +
                "the panel is positioned by Position of the UI in Azumatt.FastLink.cfg and nowhere else.");

            // A code default only applies on FIRST run: BepInEx writes the file once and it
            // then persists, and our own sync now deliberately preserves unshipped configs.
            // So flipping a default cannot disable something already enabled on a client.
            // The release must ship this plugin's cfg to control it - see the payload.
            if (ProfilePlugins.Value)
                Log.LogWarning(Tag + "plugin profiling is ENABLED by the on-disk config. "
                    + "This is a measurement mode; set ProfilePluginUpdateCost=false to play.");

            Harmony harmony = new Harmony(GUID);
            // Every subsystem is optional and independently guarded. A failure in any one of
            // them must never stop the others or abort Awake: when PluginProfiler.Install
            // threw a TypeLoadException it took the whole plugin with it, silently removing
            // jump, interaction, crouch, the menu and the misc ring in one go. Degrade,
            // never die.
            Guard("patchAll", delegate { harmony.PatchAll(typeof(NeuralyzeVRFixesPlugin).Assembly); });
            Guard("panelFrameGuard", delegate { PanelFrameGuard.Install(harmony); });
            if (MeasureCombatLatency.Value) Guard("combatLatency", delegate { CombatLatency.Install(harmony); });
            if (RestorePhysicsRate.Value) Guard("physicsRate", delegate { PhysicsRateRestorer.Install(harmony); });
            if (ProfilePlugins.Value) Guard("pluginProfiler", delegate { PluginProfiler.Install(harmony); });
            if (ProfileInventory.Value) Guard("inventoryProfiler", delegate { InventoryProfiler.Install(harmony); });
            if (ProfileGameMethods.Value) Guard("gameMethodProfiler", delegate { GameMethodProfiler.Install(harmony); });
            if (MiscMenuEnabled.Value)
            {
                Guard("miscMenuLoad", delegate { MiscMenu.Load(MiscMenuActions.Value); });
                Guard("keyPulse", delegate { KeyPulse.Install(harmony); });
                Guard("miscMenuInstall", delegate { MiscMenu.Install(harmony); });
                Guard("consoleEcho", delegate { NeuralyzeVRFixes.DirectActions.InstallCommandEcho(harmony); });
                Guard("panelInput", delegate { PanelInput.Install(harmony); });
                Guard("renderScale", delegate { SteamVrRenderScale.Apply(); });
            }
            // Outside the misc-menu block on purpose: the wrist entry is the FALLBACK for resetting
            // height, this is the primary route, and it must still work for a player who turned the
            // ring off. Subscription only - the events are rare and nothing is polled.
            Guard("systemRecenter", delegate { SystemRecenter.Install(); });
            Guard("renderScale", delegate { ApplyRenderScale(); });
            Guard("adoptCanvases", delegate { StartCoroutine(AdoptCanvasesLater()); });
            Log.LogInfo(Tag + "loaded " + Version
                + " panelGuard=" + FramePanelGuard.Value
                + " hideQuickSlots=" + HideQuickSlotBar.Value
                + " hideMinimap=" + HideMinimap.Value
                + " suppressHints=" + SuppressKeyHints.Value);
        }

        private void Guard(string what, Action body)
        {
            try { body(); }
            catch (Exception e)
            {
                Log.LogWarning(Tag + "subsystem '" + what + "' failed and was skipped: "
                    + e.GetType().Name + ": " + e.Message);
            }
        }

        // The eye texture resolution appears in no log file, yet it decides whether the
        // client is pixel-bound: frame cost scales with pixels, and stereo VR renders
        // several times the pixels of a 1080p desktop. Reported here so the question can
        // be answered from data instead of arithmetic. XRSettings lives in
        // UnityEngine.XRModule, which is not among the available reference assemblies,
        // so it is reached reflectively.
        private void ApplyRenderScale()
        {
            try
            {
                Type xr = TypeCache.Get("UnityEngine.XR.XRSettings");
                if (xr == null) { Log.LogWarning(Tag + "XRSettings not found; no render scale report"); return; }
                PropertyInfo width = xr.GetProperty("eyeTextureWidth", BindingFlags.Static | BindingFlags.Public);
                PropertyInfo height = xr.GetProperty("eyeTextureHeight", BindingFlags.Static | BindingFlags.Public);
                PropertyInfo scale = xr.GetProperty("renderViewportScale", BindingFlags.Static | BindingFlags.Public);
                object w = width == null ? null : width.GetValue(null, null);
                object h = height == null ? null : height.GetValue(null, null);
                object current = scale == null ? null : scale.GetValue(null, null);
                Log.LogInfo(Tag + "XR eyeTexture=" + w + "x" + h + " renderViewportScale=" + current);
                if (scale == null || RenderScale.Value >= 0.999f) return;
                scale.SetValue(null, RenderScale.Value, null);
                Log.LogInfo(Tag + "renderViewportScale set to " + RenderScale.Value);
            }
            catch (Exception e) { Log.LogWarning(Tag + "render scale step failed: " + e.Message); }
        }

        // Canvases are adopted on a slow poll rather than once: mod UIs are frequently
        // built on first use, long after VHVR's single startup scan has short-circuited.
        // Nothing below may run during world load. The 2.1.56 session died mid-load with
        // no managed exception, and the only new code touching HUD objects at that point
        // was this plugin deactivating hotbar roots. Gating on a live Player plus a live
        // ZNetScene keeps every mutation inside a world that has finished loading.
        internal static bool InWorld()
        {
            try { return Player.m_localPlayer != null && ZNetScene.instance != null && Hud.instance != null; }
            catch { return false; }
        }

        private void FixedUpdate()
        {
            if (MeasureCombatLatency.Value) CombatLatency.PhysicsStep();
        }

        private void Update()
        {
            HookProfiler.Frame();
            global::NeuralyzeVRFixes.DirectActions.PumpQueuedCommands();
            // Not in LateUpdate: that returns early unless a world is loaded, and FastLink's panel
            // only exists on the start screen. The first version of this shipped inside that guard
            // and never executed once.
            if (HideFastLinkTitle.Value) FastLinkTitle.Tick();
            if (Profile.Value) ProfilerHub.Tick();
            // Ticked here, not inside ProfilerHub: that hub only runs when FrameAndVrReport is on,
            // so an instrument nested in it produced nothing whenever the frame report was off - a
            // measurement switch that silently measures nothing is worse than no switch.
            if (ProfileInventory.Value) InventoryProfiler.Tick();
            if (ProfileGameMethods.Value) GameMethodProfiler.Tick();
        }

        private void LateUpdate()
        {
            if (!InWorld()) return;
            if (HideHotbarRoot.Value && !_hotbarsDone) { _hotbarsDone = true; DeactivateHotbars(); }
            InputAudit.Tick();
            SteamVRActionWatch.Tick();
            if (BridgeInput.Value) InputBridge.Tick();
            if (DirectActions.Value) DirectActionInvoker.Tick();
            if (MeasureCombatLatency.Value) CombatLatency.Tick();
            SwingWatch.Tick();
            FullActionWatch.Tick();
            BuildMenuProbe.Tick();
            HoverMenu.Tick();
            // Ungated on purpose: a wrong character height is not a diagnostic, it is the game
            // being unplayable while seated, and the watch costs one IsSitting() per frame.
            SitRecenter.Tick();
            if (SuppressKeyHints.Value || LogHoverText.Value) HoverTextSweeper.Tick();
            if (HideMinimap.Value) MinimapHider.Tick();
            if (LogJumpInput.Value) JumpInputWatch.Tick();
        }

        // Done on the slow poll rather than per frame: FindObjectsOfType returns only
        // active objects, so once a bar is deactivated it is not found again.
        private static void DeactivateHotbars()
        {
            // lint:per-frame bounded - once from LateUpdate behind _hotbarsDone, and otherwise from
            // the AdoptPollSeconds coroutine at 2 Hz; the search shrinks to nothing as bars are
            // deactivated, because FindObjectsOfType returns only active objects.
            try
            {
                foreach (HotkeyBar bar in UnityEngine.Object.FindObjectsOfType<HotkeyBar>())
                {
                    if (bar == null || !bar.gameObject.activeSelf) continue;
                    bar.gameObject.SetActive(false);
                    Log.LogInfo(Tag + "hotbar root deactivated: " + bar.gameObject.name);
                }
            }
            catch (Exception e) { Log.LogWarning(Tag + "hotbar deactivate failed: " + e.Message); }
        }

        private IEnumerator AdoptCanvasesLater()
        {
            var wanted = new HashSet<string>();
            foreach (string name in AdoptCanvases.Value.Split(','))
            {
                string trimmed = name.Trim();
                if (trimmed.Length > 0) wanted.Add(trimmed);
            }
            CompanionWristHud.ClaimNames(wanted);
            // No early return on an empty name list: this loop also drives the UI-layer sweep, the
            // hotbar deactivation and the companion wrist HUD. Shipping AdoptCanvasNames empty used
            // to kill all three silently.
            // Deliberately NOT tracking "already adopted" names. Companion panels are created on
            // demand and the radial is destroyed when it closes, so a name that was adopted once
            // will be a fresh screen-space canvas the next time it appears. EnsureAdopted skips
            // canvases already in world space, so re-checking a converted one costs a comparison.
            float period = AdoptPollSeconds.Value < 0.05f ? 0.05f : AdoptPollSeconds.Value;
            var wait = new WaitForSeconds(period);
            while (true)
            {
                yield return wait;
                VRGuiBridge.EnsureAdopted(wanted);
                if (SweepUILayer.Value && InWorld()) VRGuiBridge.SweepAdoptedCanvasLayers();
                if (HideHotbarRoot.Value && InWorld()) DeactivateHotbars();
                if (InWorld()) CompanionWristHud.Tick();
            }
        }
    }

    // A Harmony postfix on Hud.UpdateCrosshair is not sufficient on its own: any mod
    // whose own postfix sorts after ours, or which writes the label from its own
    // Update, wins. LateUpdate runs after every MonoBehaviour Update in the frame, so
    // stripping here is ordering-proof for same-frame writers. The postfix is kept as
    // well so the strip also holds for anything reading the label before LateUpdate.
    internal static class HoverTextSweeper
    {
        private static Hud _hud;
        private static FieldInfo _hoverNameField;
        private static PropertyInfo _crosshairInstance;
        private static FieldInfo _leftCloneField;
        private static readonly HashSet<string> _seen = new HashSet<string>();
        private static bool _reportedStrip;

        internal static void Tick()
        {
            try
            {
                // Label 1: Hud.m_hoverName. VHVR repoints this at its own clone
                // (CrosshairManager line 499), so it is the live centre label.
                if (_hud == null) _hud = UnityEngine.Object.FindObjectOfType<Hud>();
                if (_hud != null)
                {
                    if (_hoverNameField == null) _hoverNameField = AccessTools.Field(typeof(Hud), "m_hoverName");
                    if (_hoverNameField != null) StripComponent(_hoverNameField.GetValue(_hud), "centre");
                }

                // Label 2: CrosshairManager.hoverNameCloneLeftHand. A SEPARATE object,
                // written directly by VHVR's own Hud.UpdateCrosshair postfix
                // (HandBasedInteractionPatches line 103) from Hoverable.GetHoverText().
                // Left-hand interaction shows this one, and nothing above touches it.
                if (_crosshairInstance == null)
                {
                    Type cm = TypeCache.Get("ValheimVRMod.VRCore.UI.CrosshairManager");
                    if (cm == null) return;
                    _crosshairInstance = cm.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    if (_crosshairInstance == null) _crosshairInstance = cm.GetProperty("instance", BindingFlags.Static | BindingFlags.NonPublic);
                    _leftCloneField = AccessTools.Field(cm, "hoverNameCloneLeftHand");
                }
                if (_crosshairInstance == null || _leftCloneField == null) return;
                object cmInst = _crosshairInstance.GetValue(null, null);
                if (cmInst == null) return;
                GameObject leftLabel = _leftCloneField.GetValue(cmInst) as GameObject;
                if (leftLabel == null) return;
                foreach (Component c in leftLabel.GetComponents(typeof(Component)))
                    StripComponent(c, "leftHand");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "hover sweep failed: " + e.Message);
                NeuralyzeVRFixesPlugin.SuppressKeyHints.Value = false;
                NeuralyzeVRFixesPlugin.LogHoverText.Value = false;
            }
        }

        private static void StripComponent(object component, string which)
        {
            if (component == null) return;
            PropertyInfo textProp = component.GetType().GetProperty("text", BindingFlags.Instance | BindingFlags.Public);
            if (textProp == null || textProp.PropertyType != typeof(string) || !textProp.CanWrite) return;
            string current = textProp.GetValue(component, null) as string;
            if (string.IsNullOrEmpty(current)) return;

            if (NeuralyzeVRFixesPlugin.LogHoverText.Value && _seen.Count < 24 && _seen.Add(which + "|" + current))
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "hoverRaw[" + which + "]=" + current.Replace("\n", "\\n"));

            if (!NeuralyzeVRFixesPlugin.SuppressKeyHints.Value) return;
            string stripped = StripHoverKeyHints.StripHintLines(current);
            if (stripped == current) return;
            textProp.SetValue(component, stripped, null);
            if (!_reportedStrip)
            {
                _reportedStrip = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "hover strip active [" + which + "]: "
                    + current.Replace("\n", "\\n") + " -> " + stripped.Replace("\n", "\\n"));
            }
        }
    }

    // VHVR clones the small minimap (MinimapPanelElement line 113) and then repoints
    // Minimap.m_smallRoot at the clone, so m_smallRoot is the wrist panel, not the flat
    // one. Deactivating m_smallRoot would therefore remove exactly the map that is
    // wanted. VHVR exposes the abandoned object as the public Original property, and
    // only populates Clone once it has taken over, so gating on Clone makes this inert
    // whenever the placement is Legacy and no duplicate can exist.
    internal static class MinimapHider
    {
        private static PropertyInfo _instanceProp;
        private static FieldInfo _elementsField;
        private static bool _reported;
        private static bool _broken;

        internal static void Tick()
        {
            if (_broken) return;
            try
            {
                if (_instanceProp == null)
                {
                    Type vrHud = TypeCache.Get("ValheimVRMod.VRCore.UI.VRHud");
                    if (vrHud == null) { _broken = true; return; }
                    _instanceProp = vrHud.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    _elementsField = AccessTools.Field(vrHud, "VRHudElements");
                    if (_instanceProp == null || _elementsField == null) { _broken = true; return; }
                }
                object hud = _instanceProp.GetValue(null, null);
                if (hud == null) return;
                var elements = _elementsField.GetValue(hud) as IEnumerable;
                if (elements == null) return;
                foreach (object element in elements)
                {
                    if (element == null) continue;
                    if (element.GetType().Name != "MinimapPanelElement") continue;
                    GameObject clone = RootOf(element, "Clone");
                    if (clone == null) return;              // VHVR has not taken over: nothing to remove
                    GameObject original = RootOf(element, "Original");
                    if (original == null || !original.activeSelf) return;
                    if (ReferenceEquals(original, clone)) return;
                    original.SetActive(false);
                    if (!_reported)
                    {
                        _reported = true;
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                            + "flat-HUD minimap duplicate deactivated (kept wrist clone '" + clone.name + "')");
                    }
                    return;
                }
            }
            catch (Exception e)
            {
                _broken = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "minimap hide failed: " + e.Message);
            }
        }

        private static GameObject RootOf(object element, string propertyName)
        {
            PropertyInfo prop = element.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public);
            object panel = prop == null ? null : prop.GetValue(element, null);
            if (panel == null) return null;
            PropertyInfo root = panel.GetType().GetProperty("Root", BindingFlags.Instance | BindingFlags.Public);
            return root == null ? null : root.GetValue(panel, null) as GameObject;
        }
    }

    // Reports whether the Jump button reaches the game at all. VHVR maps Jump to the
    // SteamVR action valheim_Jump and ORs it into ZInput.GetButtonDown, so if this
    // never fires while the button is pressed the loss is upstream of the game; if it
    // fires and nothing happens, the game refused the jump.
    internal static class JumpInputWatch
    {
        private static int _count;

        // Every vanilla action VHVR claims to map, so a dead button can be told apart
        // from a dead action. Crouch and Run are bound to stick dpad directions, Map and
        // JoyMenu to stick clicks - all reported non-functional.
        private static readonly string[] Watched =
        {
            "Jump", "Crouch", "Run", "Use", "Inventory", "Map", "JoyMenu", "ToggleWalk", "Hide"
        };

        internal static void Tick()
        {
            try
            {
                if (ZInput.instance == null) return;
                if (_count >= 40) return;
                string hit = null;
                foreach (string name in Watched)
                    if (ZInput.GetButtonDown(name)) { hit = name; break; }
                if (hit == null) return;
                _count++;
                Player p = Player.m_localPlayer;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "ZInput '" + hit + "' DOWN #" + _count
                    + " player=" + (p != null)
                    + " onGround=" + (p != null && p.IsOnGround())
                    + " attached=" + (p != null && p.IsAttached())
                    + " encumbered=" + (p != null && p.IsEncumbered())
                    + " CROUCHING=" + (p != null && p.IsCrouching()));
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "jump watch failed: " + e.Message);
                NeuralyzeVRFixesPlugin.LogJumpInput.Value = false;
            }
        }
    }

    // Asks SteamVR directly, per action, whether a binding is live. VHVR maps ten
    // vanilla actions onto SteamVR boolean actions; if activeBinding is false for an
    // action then SteamVR never bound it and no amount of in-game config will help - the
    // fix is in the controller binding. If activeBinding is true but the action never
    // goes true while the button is pressed, the button itself is not being delivered.
    // Reported once, a few seconds after entering the world, so bindings have settled.
    internal static class InputAudit
    {
        private static float _dueAt = -1f;
        private static bool _done;

        internal static void Tick()
        {
            if (_done) return;
            if (_dueAt < 0f) { _dueAt = Time.realtimeSinceStartup + 6f; return; }
            if (Time.realtimeSinceStartup < _dueAt) return;
            _done = true;
            try
            {
                Type vc = TypeCache.Get("ValheimVRMod.VRCore.UI.VRControls");
                if (vc == null) { NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "VRControls not found"); return; }
                PropertyInfo instProp = vc.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                object inst = instProp == null ? null : instProp.GetValue(null, null);
                if (inst == null) { NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "VRControls.instance null"); return; }
                FieldInfo mapField = AccessTools.Field(vc, "zInputToBooleanAction");
                var map = mapField == null ? null : mapField.GetValue(inst) as IDictionary;
                if (map == null) { NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "zInputToBooleanAction unreadable"); return; }

                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "=== SteamVR binding audit (" + map.Count + " mapped actions) ===");
                // These are not in VHVR's ZInput dictionary, so the loop below never
                // reaches them - yet ToggleCrouch is exactly the one reported missing.
                Type sva = TypeCache.Get("Valve.VR.SteamVR_Actions");
                foreach (string extra in new[] { "valheim_ToggleCrouch", "valheim_ToggleRun", "valheim_Grab", "valheim_UseLeft" })
                {
                    PropertyInfo ep = sva == null ? null : sva.GetProperty(extra, BindingFlags.Static | BindingFlags.Public);
                    object ea = ep == null ? null : ep.GetValue(null, null);
                    if (ea == null) { NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "  " + extra + " -> <action missing>"); continue; }
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "  " + extra
                        + " activeBinding=" + Read(ea, "activeBinding")
                        + " ORIGIN=" + (SteamVRProbe.Init() ? SteamVRProbe.Origin(ea) : "<probe unavailable>"));
                }
                foreach (DictionaryEntry entry in map)
                {
                    var actions = entry.Value as IEnumerable;
                    if (actions == null) continue;
                    foreach (object action in actions)
                    {
                        if (action == null) { NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "  " + entry.Key + " -> <null action>"); continue; }
                        string origin = SteamVRProbe.Init() ? SteamVRProbe.Origin(action) : "<probe unavailable>";
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "  " + entry.Key
                            + " -> path=" + Read(action, "fullPath")
                            + " activeBinding=" + Read(action, "activeBinding")
                            + " active=" + Read(action, "active")
                            + " ORIGIN=" + origin);
                        SteamVRActionWatch.Register(Convert.ToString(entry.Key), action);
                    }
                }
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "input audit failed: " + e.Message);
            }
        }

        private static string Read(object target, string member)
        {
            try
            {
                PropertyInfo p = target.GetType().GetProperty(member, BindingFlags.Instance | BindingFlags.Public);
                if (p != null) return Convert.ToString(p.GetValue(target, null));
                FieldInfo f = target.GetType().GetField(member, BindingFlags.Instance | BindingFlags.Public);
                if (f != null) return Convert.ToString(f.GetValue(target));
                return "n/a";
            }
            catch (Exception e) { return "err:" + e.GetType().Name; }
        }
    }

    // Asks SteamVR which PHYSICAL input is bound to each action. The 1.8.0 audit showed
    // activeBinding=True for Jump, Use, Inventory and ToggleMap while none of those
    // buttons did anything and ZInput never reported a single press - so the action is
    // bound and its set is active, yet the press never arrives. The remaining unknown is
    // which origin SteamVR actually attached, i.e. whether the loaded controller profile
    // is really oculus_touch or a fallback whose json has no A/X/stick-click at all.
    internal static class SteamVRProbe
    {
        private static Type _srcType;
        internal static object Any, Left, Right;

        internal static bool Init()
        {
            if (_srcType != null) return true;
            _srcType = TypeCache.Get("Valve.VR.SteamVR_Input_Sources");
            if (_srcType == null) return false;
            try
            {
                Any = Enum.Parse(_srcType, "Any");
                Left = Enum.Parse(_srcType, "LeftHand");
                Right = Enum.Parse(_srcType, "RightHand");
                return true;
            }
            catch { _srcType = null; return false; }
        }

        // Invokes by name and argument count, because the SteamVR plugin exposes both
        // no-arg and per-source overloads depending on version.
        private static readonly System.Collections.Generic.Dictionary<string, MethodInfo> _methods
            = new System.Collections.Generic.Dictionary<string, MethodInfo>();

        internal static object Call(object target, string name, params object[] args)
        {
            if (target == null) return null;
            // Call(x, "M", null) binds null to THIS array, not to a single null argument,
            // so args.Length threw and the caller's catch reported a fabricated zero.
            // That defect produced the "hand speed 0.00 m/s" finding twice.
            if (args == null) args = new object[] { null };
            // Cached by (type, name, arity). GetMethods() allocates a fresh MethodInfo[] on every
            // call and this runs on the input path several times per frame - it was measurable as
            // lost frames, the same mistake as the name-based lookups fixed in 1.59.
            Type owner = target.GetType();
            string key = owner.FullName + "|" + name + "|" + args.Length;
            MethodInfo found;
            if (!_methods.TryGetValue(key, out found))
            {
                foreach (MethodInfo m in owner.GetMethods(BindingFlags.Instance | BindingFlags.Public))
                {
                    if (m.Name != name || m.GetParameters().Length != args.Length) continue;
                    found = m;
                    break;
                }
                _methods[key] = found;
            }
            if (found == null) return null;
            try { return found.Invoke(target, args); } catch { return null; }
        }

        internal static string Origin(object action)
        {
            object localized = Call(action, "GetLocalizedOrigin", Any);
            if (localized == null) localized = Call(action, "GetLocalizedOrigin");
            object handle = null;
            PropertyInfo ao = action.GetType().GetProperty("activeOrigin", BindingFlags.Instance | BindingFlags.Public);
            if (ao != null) { try { handle = ao.GetValue(action, null); } catch { } }
            string text = Convert.ToString(localized);
            if (string.IsNullOrEmpty(text)) text = "<no origin>";
            return text + " (originHandle=" + Convert.ToString(handle) + ")";
        }
    }

    // Watches the SteamVR actions directly, bypassing ZInput entirely. If a press shows
    // up here but not in ZInput, the loss is inside VHVR's ZInput bridge. If it shows up
    // in neither, SteamVR is not delivering the button and the fix is the binding.
    internal static class SteamVRActionWatch
    {
        private static readonly List<KeyValuePair<string, object>> _actions = new List<KeyValuePair<string, object>>();
        private static bool _built;
        private static int _logged;

        internal static void Tick()
        {
            if (_logged >= 60) return;
            if (!_built) return;
            foreach (KeyValuePair<string, object> pair in _actions)
            {
                Check(pair.Key, pair.Value, "L", SteamVRProbe.Left);
                Check(pair.Key, pair.Value, "R", SteamVRProbe.Right);
            }
        }

        private static void Check(string name, object action, string hand, object source)
        {
            object down = SteamVRProbe.Call(action, "GetStateDown", source);
            if (!(down is bool) || !(bool)down) return;
            if (_logged >= 60) return;
            _logged++;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "STEAMVR press: " + name + " hand=" + hand);
        }

        internal static void Register(string key, object action)
        {
            if (action == null) return;
            _actions.Add(new KeyValuePair<string, object>(key, action));
            _built = true;
        }
    }

    // Bridges SteamVR actions straight to ZInput via VHVR's EmulateButtonDown, which its
    // own prefix honours before any gating. Fixes the actions VHVR silently drops without
    // patching VHVR or touching its gate logic.
    internal static class InputBridge
    {
        // SteamVR_Actions static property -> the vanilla ZInput name(s) to raise.
        private static readonly string[][] Bridged =
        {
            new[] { "valheim_Jump",            "Jump" },
            new[] { "valheim_ToggleInventory", "Inventory" },
            new[] { "valheim_ToggleMap",       "Map" },
            new[] { "valheim_ToggleMenu",      "JoyMenu" },
            new[] { "valheim_ToggleCrouch",    "Crouch" },
            new[] { "valheim_ToggleRun",       "Run" },
        };

        private static readonly List<KeyValuePair<object, string>> _pairs = new List<KeyValuePair<object, string>>();
        private static MethodInfo _emulate;
        private static bool _ready, _failed;
        private static int _logged;

        private static bool Prepare()
        {
            if (_ready) return true;
            if (_failed) return false;
            try
            {
                if (!SteamVRProbe.Init()) { _failed = true; return false; }
                Type actions = TypeCache.Get("Valve.VR.SteamVR_Actions");
                Type patch = TypeCache.Get("ValheimVRMod.Patches.ZInput_GetButtonDown_Patch");
                if (actions == null || patch == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "input bridge unavailable (actions=" + (actions != null) + " patch=" + (patch != null) + ")");
                    _failed = true; return false;
                }
                _emulate = patch.GetMethod("EmulateButtonDown", BindingFlags.Static | BindingFlags.Public,
                    null, new[] { typeof(string) }, null);
                if (_emulate == null) { NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "EmulateButtonDown not found"); _failed = true; return false; }

                foreach (string[] row in Bridged)
                {
                    PropertyInfo prop = actions.GetProperty(row[0], BindingFlags.Static | BindingFlags.Public);
                    object action = prop == null ? null : prop.GetValue(null, null);
                    if (action == null) { NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "no SteamVR action " + row[0]); continue; }
                    _pairs.Add(new KeyValuePair<object, string>(action, row[1]));
                }
                _ready = _pairs.Count > 0;
                if (_ready) NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "input bridge armed for " + _pairs.Count + " actions");
                else _failed = true;
                return _ready;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "input bridge setup failed: " + e.Message);
                _failed = true; return false;
            }
        }

        internal static void Tick()
        {
            if (!Prepare()) return;
            foreach (KeyValuePair<object, string> pair in _pairs)
            {
                if (!Down(pair.Key)) continue;
                try
                {
                    _emulate.Invoke(null, new object[] { pair.Value });
                    if (_logged < 30)
                    {
                        _logged++;
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "bridged '" + pair.Value + "'");
                    }
                }
                catch (Exception e)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "bridge invoke failed: " + e.Message);
                    _failed = true; return;
                }
            }
        }

        // Any-source first; some actions only report on a specific hand.
        private static bool Down(object action)
        {
            object r = SteamVRProbe.Call(action, "GetStateDown", SteamVRProbe.Any);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(action, "GetStateDown", SteamVRProbe.Left);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(action, "GetStateDown", SteamVRProbe.Right);
            return r is bool && (bool)r;
        }
    }

    // Invokes gameplay methods directly. This exists because every input-layer route
    // failed for a measurable reason: VHVR gates Jump behind canJump() and keeps Crouch
    // and Run on a hardcoded ignore list, and its EmulateButtonDown hook - which does
    // fire - is a single-consumption HashSet that any of the ~100 installed mods can
    // drain before the player's own input read. Calling the game has no race.
    internal static class DirectActionInvoker
    {
        private static bool _ready, _failed;
        private static object _jump, _crouch, _use, _inv, _map, _grab, _walk, _menu, _dodge, _buildMenu;
        private static MethodInfo _mDodge, _mInDodge, _mStartAttack, _mInAttack, _mTogglePieces, _mInPlaceMode, _mRemovePiece;
        private static FieldInfo _fCrouchToggled;
        private static MethodInfo _mJump, _mSetCrouch, _mIsCrouching, _mOnGround, _mIsAttached, _mInteract, _mSetMapMode;
        private static FieldInfo _fHovering;
        private static int _logged;
        private static float _nextState;

        private static bool Prepare()
        {
            if (_ready) return true;
            if (_failed) return false;
            try
            {
                if (!SteamVRProbe.Init()) { _failed = true; return false; }
                Type actions = TypeCache.Get("Valve.VR.SteamVR_Actions");
                if (actions == null) { _failed = true; return false; }
                _jump   = Get(actions, "valheim_Jump");
                _grab   = Get(actions, "valheim_Grab");
                _walk   = Get(actions, "valheim_Walk");
                _crouch = Get(actions, "valheim_ToggleCrouch");
                _use    = Get(actions, "valheim_Use");
                _inv    = Get(actions, "valheim_ToggleInventory");
                _map    = Get(actions, "valheim_ToggleMap");
                _menu   = Get(actions, "valheim_ToggleMenu");
                // The build menu gets the same treatment dodge did, and for the same
                // reason: the game never sees the press. Player.UpdateBuildGuiInput reads
                // the vanilla "BuildMenu" button, and measurement shows that button is
                // never down in VR even while laserPointers_RightClick is bound, active and
                // firing - so repairing the game's own path is pointless. Read the action
                // and call Hud.TogglePieceSelection directly.
                _buildMenu = Get(actions, "laserPointers_RightClick");
                _useLeft   = Get(actions, "valheim_UseLeft");
                _dodge  = Get(actions, "valheim_Dodge");

                _mJump        = AccessTools.Method(typeof(Character), "Jump", new Type[0]) ?? AccessTools.Method(typeof(Character), "Jump");
                _mSetCrouch   = AccessTools.Method(typeof(Character), "SetCrouch", new[] { typeof(bool) });
                _mIsCrouching = AccessTools.Method(typeof(Character), "IsCrouching");
                _mOnGround    = AccessTools.Method(typeof(Character), "IsOnGround");
                _mIsAttached  = AccessTools.Method(typeof(Character), "IsAttached");
                _mInteract    = AccessTools.Method(typeof(Player), "Interact");
                _mSetMapMode  = AccessTools.Method(typeof(Minimap), "SetMapMode");
                _fHovering    = AccessTools.Field(typeof(Player), "m_hovering");
                _mDodge       = AccessTools.Method(typeof(Player), "Dodge");
                _mInDodge     = AccessTools.Method(typeof(Character), "InDodge");
                _mTogglePieces = AccessTools.Method(typeof(Hud), "TogglePieceSelection");
                _mInPlaceMode  = AccessTools.Method(typeof(Player), "InPlaceMode");
                _mRemovePiece  = AccessTools.Method(typeof(Player), "RemovePiece");
                _mStartAttack = AccessTools.Method(typeof(Humanoid), "StartAttack");
                _mInAttack    = AccessTools.Method(typeof(Character), "InAttack");
                _fCrouchToggled = AccessTools.Field(typeof(Player), "m_crouchToggled");

                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "direct actions ready"
                    + " jump=" + (_mJump != null) + " crouch=" + (_mSetCrouch != null)
                    + " interact=" + (_mInteract != null) + " hovering=" + (_fHovering != null)
                    + " mapmode=" + (_mSetMapMode != null));
                _ready = _mJump != null || _mSetCrouch != null || _mInteract != null;
                if (!_ready) _failed = true;
                return _ready;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "direct action setup failed: " + e.Message);
                _failed = true; return false;
            }
        }

        private static object Get(Type t, string name)
        {
            PropertyInfo p = t.GetProperty(name, BindingFlags.Static | BindingFlags.Public);
            return p == null ? null : p.GetValue(null, null);
        }

        private static bool Down(object action)
        {
            if (action == null) return false;
            object r = SteamVRProbe.Call(action, "GetStateDown", SteamVRProbe.Any);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(action, "GetStateDown", SteamVRProbe.Left);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(action, "GetStateDown", SteamVRProbe.Right);
            return r is bool && (bool)r;
        }

        // Level, not edge. VHVR delivers VR input to the game by OR-ing
        // action.GetStateDown into ZInput.GetButtonDown, and measurement shows the rising
        // edge for laserPointers_RightClick is never observed there: the action's level
        // goes true - the audit records it firing - while ZInput.GetButtonDown("BuildMenu")
        // stays false on every frame, so Valheim never opens piece selection. Reading the
        // level and deriving the edge here sidesteps that entirely.
        private static bool Held(object action)
        {
            if (action == null) return false;
            object r = SteamVRProbe.Call(action, "GetState", SteamVRProbe.Any);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(action, "GetState", SteamVRProbe.Right);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(action, "GetState", SteamVRProbe.Left);
            return r is bool && (bool)r;
        }

        // The authoritative crouch state. Player::IsCrouching compares the current
        // animation hash against s_animatorTagCrouch, so it reports false whenever the
        // crouch animation is not the active clip even though the character is crouched.
        private static string Latch(Player p)
        {
            try
            {
                if (_fCrouchToggled == null) return "n/a";
                return Convert.ToString(Convert.ToBoolean(_fCrouchToggled.GetValue(p)));
            }
            catch { return "err"; }
        }

        private static string FieldFlag(object target, string field)
        {
            try
            {
                FieldInfo f = AccessTools.Field(target.GetType(), field);
                if (f == null) return "n/a";
                return Convert.ToString(Convert.ToBoolean(f.GetValue(target)));
            }
            catch { return "err"; }
        }

        private static bool Flag(MethodInfo m, object target)
        {
            try { object r = m == null ? null : m.Invoke(target, null); return r is bool && (bool)r; }
            catch { return false; }
        }

        internal static void Tick()
        {
            if (!Prepare()) return;
            Player p = Player.m_localPlayer;
            if (p == null) return;
            try
            {
                // Periodic state, so a refused jump can be explained instead of guessed at.
                if (Time.realtimeSinceStartup >= _nextState)
                {
                    _nextState = Time.realtimeSinceStartup + 5f;
                    if (_logged < 40)
                    {
                        _logged++;
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "state onGround=" + Flag(_mOnGround, p)
                            + " crouchLatch=" + Latch(p)
                            + " crouchAnim=" + Flag(_mIsCrouching, p)
                            + " run=" + FieldFlag(p, "m_run")
                            + " inPlaceMode=" + Flag(AccessTools.Method(typeof(Player), "InPlaceMode"), p)
                            + " blocking=" + Flag(AccessTools.Method(typeof(Character), "IsBlocking"), p)
                            + " attached=" + Flag(_mIsAttached, p)
                            + " takeInput=" + Flag(AccessTools.Method(typeof(Character), "TakeInput"), p)
                            + " hovering=" + (Hover(p) != null)
                            // The build menu lives only in the LaserPointers action set, and VHVR
                            // activates that set only while a laser pointer is active - which
                            // shouldLaserPointersBeActive() ties to place mode. If pointer is false
                            // while inPlaceMode is true, the set never activates, a tap of the
                            // BuildMenu button never reaches Valheim, and the shared button falls
                            // through to QuickSwitch. That is the difference between "the menu is
                            // hidden" and "the button is dead", so it is recorded here.
                            + " pointer=" + BuildMenuProbe.PointerActive()
                            + " laserSet=" + BuildMenuProbe.LaserSetActive()
                            + " pieceMenu=" + BuildMenuProbe.PieceSelectionVisible()
                            + (FlyControls.Flying() ? "  FLY " + FlyControls.Gates() : ""));
                    }
                }

                if (Down(_jump) && NeuralyzeVRFixesPlugin.GripJumpDodges.Value && GrabHeld() && _mDodge != null)
                {
                    if (!Flag(_mInDodge, p))
                    {
                        Vector3 dir = DodgeDirection(p);
                        _mDodge.Invoke(p, DodgeArgs(dir));
                        Say("DODGE invoked dir=" + dir.ToString("F2"));
                    }
                }
                else if (Down(_jump) && RemovePieceTap(p))
                {
                    // Handled as a removal; jump is deliberately not invoked, mirroring
                    // VHVR's own rule that removing a piece outranks jumping.
                }
                else if (Down(_jump))
                {
                    bool ground = Flag(_mOnGround, p), attached = Flag(_mIsAttached, p), crouch = Flag(_mIsCrouching, p);
                    if (crouch && _mSetCrouch != null) _mSetCrouch.Invoke(p, new object[] { false });
                    // Seated, A stands you up.
                    //
                    // Jumping is refused while attached anyway - the log recorded exactly that while
                    // the player was stuck on a raft's rudder - so the button is free, and "press A
                    // to get off" needs no explaining. The grip cannot be used for this: it IS the
                    // rudder, VHVR steers on isSingleGrabbing.
                    if (attached || DirectActions.AtHelm())
                    {
                        Say("A while seated -> release (attached=" + attached + " atHelm=" + DirectActions.AtHelm() + ")");
                        DirectActions.ReleaseMount();
                    }
                    else if (ground && _mJump != null)
                    {
                        _mJump.Invoke(p, _mJump.GetParameters().Length == 0 ? null : new object[] { false });
                        Say("JUMP invoked");
                    }
                    else Say("jump refused: onGround=" + ground + " attached=" + attached + " wasCrouching=" + crouch);
                }

                if (Down(_crouch) && NeuralyzeVRFixesPlugin.DirectCrouch.Value)
                {
                    // SetCrouch alone does not hold: Player.UpdateCrouch runs every frame
                    // and drives crouch from m_crouchToggled, which vanilla flips on a
                    // Crouch keypress. VHVR keeps "Crouch" on its ZInput ignore list, so
                    // that flip never happens and the state reverted immediately - three
                    // CROUCH -> True in a row with no False proves it. Flip the latch
                    // itself, which is exactly what the keypress does.
                    if (_fCrouchToggled != null)
                    {
                        bool want = !Convert.ToBoolean(_fCrouchToggled.GetValue(p));
                        _fCrouchToggled.SetValue(p, want);
                        if (_mSetCrouch != null) _mSetCrouch.Invoke(p, new object[] { want });
                        Say("CROUCH latch -> " + want);
                    }
                    else if (_mSetCrouch != null && _mIsCrouching != null)
                    {
                        bool now = Flag(_mIsCrouching, p);
                        _mSetCrouch.Invoke(p, new object[] { !now });
                        Say("CROUCH -> " + (!now) + " (no latch field)");
                    }
                }

                // The right laser pointer already resolves Player.m_hovering (VHVR
                // HandBasedInteractionPatches line 159); only the press was missing.
                if (Down(_use) && _mInteract != null)
                {
                    // Prefer our own pointer raycast: VHVR nulls its hover result whenever
                    // the target is beyond interact range of the eye, which is most of the
                    // time when aiming a laser.
                    GameObject target = LaserTarget(p) ?? Hover(p) as GameObject;
                    if (target != null)
                    {
                        _mInteract.Invoke(p, Args(_mInteract, target));
                        Say("INTERACT invoked on " + target.name);
                        // Silent refusals are the expensive kind: say which gate closed.
                        DirectActions.ExplainHelm(target);
                    }
                    else if (NeuralyzeVRFixesPlugin.TriggerAttacks.Value && _mStartAttack != null)
                    {
                        // Nothing to interact with, so swing. VHVR only ever triggers
                        // attacks from swing momentum, and the measured hand speed is
                        // 0.00 m/s on both hands across every sample - the estimator
                        // feeds it nothing, so no physical swing can ever pass
                        // hasMomentum. This calls the vanilla attack entry point.
                        if (!Flag(_mInAttack, p))
                        {
                            string published = PublishHitStatics(p);
                            object result = _mStartAttack.Invoke(p, AttackArgs(false));
                            Say("ATTACK invoked -> " + Convert.ToString(result)
                                + " inAttack=" + Flag(_mInAttack, p) + " target=" + published);
                        }
                    }
                    else Say("use pressed but nothing hovered or in laser path");
                }

                if (Down(_inv)) ToggleInventory();
                if (Down(_menu)) ToggleMenu();
                // valheim_Dodge is declared in the action manifest but VHVR bound it to
                // nothing and never reads it, so dodge had no input at all once grip+jump was
                // disabled for breaking jump. Now bound to right-stick-down.
                //
                // Which is the same stick, and the same direction, the hover menu uses to move
                // its highlight - so the first live session dodged on every downward push while
                // choosing an action. THIS read is the route that produced it, so it is gated
                // here at the source rather than anywhere downstream: Down() is an edge
                // (GetStateDown), so an edge dropped while the list is open is dropped, not
                // deferred, and no roll fires when the grip is released. Dodge_WhileMenuOpen in
                // PanelInput.cs closes the same window for every other route into Player.Dodge.
                //
                // The LARGE MAP is gated here too, and the reason is the same input collision one
                // step further out: right-stick-down is ALSO the large map's zoom-out (grab + stick
                // through DirectActions.Zoom), so reading the map and zooming out rolled the
                // character. Reported as "when the large map is open and you are using grab +
                // up/down on right thumbstick, it will dodge on the zoom out as well". MapState
                // reads Minimap.m_mode == MapMode.Large, the game's own large-map test.
                if (Down(_dodge) && !HoverMenu.MenuOpen && !MapState.LargeMapOpen()
                    && _mDodge != null && !Flag(_mInDodge, p))
                {
                    Vector3 dir = DodgeDirection(p);
                    _mDodge.Invoke(p, DodgeArgs(dir));
                    Say("DODGE invoked dir=" + dir.ToString("F2") + " (stick)");
                }
                BuildMenuTap(p);
                if (Down(_map)) ToggleMap();
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "direct action failed: " + e.Message);
                _failed = true;
            }
        }

        // Opens the build menu, because nothing else can.
        //
        // Valheim opens piece selection from Player.UpdateBuildGuiInput, which reads the
        // vanilla button "BuildMenu". VHVR maps that name to laserPointers_RightClick (the
        // B button) and injects VR input by OR-ing action.GetStateDown into
        // ZInput.GetButtonDown. Measured on this client, with a hammer equipped and every
        // VHVR precondition satisfied - place mode true, pointer active, LaserPointers set
        // active, the action itself firing four times - ZInput.GetButtonDown("BuildMenu")
        // was false on every single frame. The level is delivered; the edge is not. So the
        // game is never told the button was pressed, and no repair inside Valheim's or
        // VHVR's input path can help.
        //
        // This reads the action's level, derives the edge here, and calls
        // Hud.TogglePieceSelection directly - the same approach dodge uses for the same
        // reason.
        //
        // Tap versus hold matches VHVR's own arbitration of that shared button
        // (VRControls.checkQuickItems): it accumulates a timer while RightClick is held in
        // place mode and opens the quick-select radial once that passes 0.3s. So the menu
        // is toggled on RELEASE and only for a press shorter than that, leaving hold to the
        // radial exactly as before. Grip held forces the radial in VHVR, so a grip-held
        // press is ignored here too.
        // Removes the piece you are pointing at, because our own jump swallows the press.
        //
        // VHVR gates removal on canRemovePiece(): place mode, the RIGHT grip held, a live
        // BuildingManager, and no move/precise-move/hold-place in progress. It then raises
        // the vanilla button "Remove", which VHVR maps to the same action as Jump, and
        // canJump() returns false whenever canRemovePiece() is true - "Removing piece takes
        // higher priority than jump" in its own words.
        //
        // That arbitration lives in VHVR's ZInput path, which this plugin bypasses: jump is
        // invoked directly because VHVR's path does not deliver it either. The measured
        // result was three JUMP invocations while trying to remove, and no removal. So the
        // same priority has to be implemented here: with a build tool out and the right grip
        // held, an A press removes rather than jumps.
        private static bool RemovePieceTap(Player p)
        {
            if (NeuralyzeVRFixesPlugin.GripRemovesPiece == null
                || !NeuralyzeVRFixesPlugin.GripRemovesPiece.Value
                || _mRemovePiece == null) return false;
            if (!Flag(_mInPlaceMode, p)) return false;
            if (!RightGrabHeld()) return false;
            object result = _mRemovePiece.Invoke(p, null);
            Say("REMOVE PIECE invoked -> " + Convert.ToString(result));
            return true;
        }

        // Right hand specifically. GrabHeld() accepts either hand, which is right for the
        // dodge chord but wrong here: VHVR requires the right grip, and the left grip is the
        // snap/reference-point modifier while building.
        // Exposed for the fly patches: flight reads the jump and crouch inputs as held
        // levels, not presses, and those patches must answer from the same actions this
        // class already owns.
        // Resolved once in Prepare(). The first version of this called
        // AccessTools.TypeByName every frame, which walks every loaded assembly - with 115
        // plugins loaded that alone destroyed the frame rate.
        private static object _useLeft;
        internal static bool UseLeftHeld()
        {
            if (_useLeft == null) return false;
            // Read the hand source AND Any. valheim_UseLeft is bound only to the left trigger, so
            // Any cannot mean the other hand - and VHVR itself never reads this action per-hand, it
            // uses .state (Any) everywhere. A per-hand read that silently returns false is the kind
            // of failure that produced a whole test session with no menu and no log line.
            object r = SteamVRProbe.Call(_useLeft, "GetState", SteamVRProbe.Left);
            if (r is bool && (bool)r) return true;
            object a = SteamVRProbe.Call(_useLeft, "GetState", SteamVRProbe.Any);
            return a is bool && (bool)a;
        }
        internal static bool LeftGrabHeld() { object r = SteamVRProbe.Call(_grab, "GetState", SteamVRProbe.Left); return r is bool && (bool)r; }
        internal static GameObject PointedAt(Player p) { return LaserTarget(p) ?? Hover(p) as GameObject; }
        internal static bool JumpHeld()   { return Held(_jump); }
        internal static bool CrouchHeld() { return Held(_crouch); }

        internal static bool RightGrabHeld()
        {
            object r = SteamVRProbe.Call(_grab, "GetState", SteamVRProbe.Right);
            return r is bool && (bool)r;
        }

        private const float BuildMenuTapSeconds = 0.3f;
        private static float _buildMenuHeldSince = -1f;

        private static void BuildMenuTap(Player p)
        {
            if (NeuralyzeVRFixesPlugin.BuildMenuTapOpens == null
                || !NeuralyzeVRFixesPlugin.BuildMenuTapOpens.Value
                || _mTogglePieces == null || _buildMenu == null) return;

            bool held = Held(_buildMenu);
            if (held)
            {
                if (_buildMenuHeldSince < 0f) _buildMenuHeldSince = Time.realtimeSinceStartup;
                return;
            }
            if (_buildMenuHeldSince < 0f) return;

            float duration = Time.realtimeSinceStartup - _buildMenuHeldSince;
            _buildMenuHeldSince = -1f;
            if (duration >= BuildMenuTapSeconds) return;      // a hold: VHVR's radial owns it
            if (GrabHeld()) return;                           // grip+press also means the radial
            if (!Flag(_mInPlaceMode, p)) return;              // only with a build tool out

            object hud = Get(typeof(Hud), "instance");
            if (hud == null) return;
            _mTogglePieces.Invoke(hud, null);
            Say("BUILD MENU toggled after " + duration.ToString("F2") + "s tap -> visible="
                + BuildMenuProbe.PieceSelectionVisible());
        }

        // VHVR's Attack.Start prefix is the whole melee damage path and it reads these
        // three statics. Without a collider it bails at CollisionPatches.cs:116-119 with
        // __result already true, which is silent success and no damage. FistCollision sets
        // exactly these three before calling Start (FistCollision.cs:445-447), so we mirror
        // it: point, direction, collider.
        private static string PublishHitStatics(Player p)
        {
            try
            {
                Type statics = TypeCache.Get("ValheimVRMod.Utilities.StaticObjects");
                if (statics == null) return "<StaticObjects missing>";
                FieldInfo fCol = AccessTools.Field(statics, "lastHitCollider");
                FieldInfo fPos = AccessTools.Field(statics, "lastHitPoint");
                FieldInfo fDir = AccessTools.Field(statics, "lastHitDir");
                if (fCol == null || fPos == null || fDir == null) return "<statics incomplete>";

                Vector3 origin, direction;
                if (!AimRay(p, out origin, out direction)) return "<no aim source>";

                RaycastHit[] hits = Physics.RaycastAll(origin, direction, NeuralyzeVRFixesPlugin.AttackReach.Value);
                if (hits == null || hits.Length == 0)
                {
                    fCol.SetValue(null, null);
                    return "<nothing in reach>";
                }
                Array.Sort(hits, delegate (RaycastHit a, RaycastHit b) { return a.distance.CompareTo(b.distance); });
                foreach (RaycastHit hit in hits)
                {
                    if (hit.collider == null) continue;
                    if (hit.collider.attachedRigidbody != null &&
                        hit.collider.attachedRigidbody.gameObject == p.gameObject) continue;
                    if (hit.collider.transform != null && hit.collider.transform.IsChildOf(p.transform)) continue;

                    fPos.SetValue(null, hit.point);
                    fDir.SetValue(null, direction.normalized);
                    fCol.SetValue(null, hit.collider);
                    Character c = hit.collider.GetComponentInParent<Character>();
                    return hit.collider.name + (c != null ? " [" + c.m_name + "]" : "")
                        + " @" + hit.distance.ToString("F2") + "m";
                }
                fCol.SetValue(null, null);
                return "<only self in reach>";
            }
            catch (Exception e) { return "<error " + e.Message + ">"; }
        }

        // Prefer the right hand's own aim so a swing lands where the hand points; fall back
        // to the head so the attack still works if the pointer is unavailable.
        private static bool AimRay(Player p, out Vector3 origin, out Vector3 direction)
        {
            origin = Vector3.zero; direction = Vector3.forward;
            try
            {
                Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");
                PropertyInfo rp = vrp == null ? null : vrp.GetProperty("rightPointer", BindingFlags.Static | BindingFlags.Public);
                object pointer = rp == null ? null : rp.GetValue(null, null);
                if (pointer != null)
                {
                    PropertyInfo posP = pointer.GetType().GetProperty("rayStartingPosition", BindingFlags.Instance | BindingFlags.Public);
                    PropertyInfo dirP = pointer.GetType().GetProperty("rayDirection", BindingFlags.Instance | BindingFlags.Public);
                    if (posP != null && dirP != null)
                    {
                        origin = (Vector3)posP.GetValue(pointer, null);
                        direction = ((Quaternion)dirP.GetValue(pointer, null)) * Vector3.forward;
                        return true;
                    }
                }
                Camera cam = Camera.main;
                if (cam != null) { origin = cam.transform.position; direction = cam.transform.forward; return true; }
                FieldInfo eye = AccessTools.Field(typeof(Player), "m_eye");
                Transform t = eye == null ? null : eye.GetValue(p) as Transform;
                if (t != null) { origin = t.position; direction = t.forward; return true; }
                return false;
            }
            catch { return false; }
        }

        private static object[] AttackArgs(bool secondary)
        {
            ParameterInfo[] ps = _mStartAttack.GetParameters();
            object[] a = new object[ps.Length];
            for (int i = 0; i < ps.Length; i++)
            {
                Type pt = ps[i].ParameterType;
                if (pt == typeof(bool)) a[i] = secondary;
                else if (pt.IsValueType) a[i] = Activator.CreateInstance(pt);
                else a[i] = null;
            }
            return a;
        }

        private static bool GrabHeld()
        {
            object r = SteamVRProbe.Call(_grab, "GetState", SteamVRProbe.Left);
            if (r is bool && (bool)r) return true;
            r = SteamVRProbe.Call(_grab, "GetState", SteamVRProbe.Right);
            return r is bool && (bool)r;
        }

        // Follows the movement stick so a dodge goes where you are leaning; a centred
        // stick rolls backwards, which is the usual evasive intent.
        private static Vector3 DodgeDirection(Player p)
        {
            Vector2 axis = Vector2.zero;
            object a = SteamVRProbe.Call(_walk, "GetAxis", SteamVRProbe.Any);
            if (a is Vector2) axis = (Vector2)a;
            Transform t = p.transform;
            Vector3 dir = t.right * axis.x + t.forward * axis.y;
            if (dir.sqrMagnitude < 0.04f) dir = -t.forward;
            dir.y = 0f;
            return dir.normalized;
        }

        private static object[] DodgeArgs(Vector3 dir)
        {
            ParameterInfo[] ps = _mDodge.GetParameters();
            object[] a = new object[ps.Length];
            for (int i = 0; i < ps.Length; i++)
                a[i] = ps[i].ParameterType == typeof(Vector3) ? (object)dir
                     : ps[i].ParameterType.IsValueType ? Activator.CreateInstance(ps[i].ParameterType) : null;
            return a;
        }

        private static object[] Args(MethodInfo m, object target)
        {
            ParameterInfo[] ps = m.GetParameters();
            object[] a = new object[ps.Length];
            a[0] = target;
            for (int i = 1; i < ps.Length; i++) a[i] = ps[i].ParameterType == typeof(bool) ? (object)false : null;
            return a;
        }

        // Mirrors VHVR's UpdateHoverObject raycast but without its eye-distance gate, and
        // requires a Hoverable so we never "interact" with bare terrain.
        private static GameObject LaserTarget(Player p)
        {
            try
            {
                Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");
                PropertyInfo rp = vrp == null ? null : vrp.GetProperty("rightPointer", BindingFlags.Static | BindingFlags.Public);
                object pointer = rp == null ? null : rp.GetValue(null, null);
                if (pointer == null) return null;

                PropertyInfo posP = pointer.GetType().GetProperty("rayStartingPosition", BindingFlags.Instance | BindingFlags.Public);
                PropertyInfo dirP = pointer.GetType().GetProperty("rayDirection", BindingFlags.Instance | BindingFlags.Public);
                if (posP == null || dirP == null) return null;
                Vector3 start = (Vector3)posP.GetValue(pointer, null);
                Quaternion rot = (Quaternion)dirP.GetValue(pointer, null);
                Vector3 dir = rot * Vector3.forward;

                FieldInfo maskF = AccessTools.Field(typeof(Player), "m_interactMask");
                int mask = maskF == null ? ~0 : Convert.ToInt32(maskF.GetValue(p));

                RaycastHit[] hits = Physics.RaycastAll(start, dir, NeuralyzeVRFixesPlugin.LaserReach.Value, mask);
                if (hits == null || hits.Length == 0) return null;
                Array.Sort(hits, delegate (RaycastHit a, RaycastHit b) { return a.distance.CompareTo(b.distance); });
                foreach (RaycastHit hit in hits)
                {
                    if (hit.collider == null) continue;
                    if (hit.collider.attachedRigidbody != null &&
                        hit.collider.attachedRigidbody.gameObject == p.gameObject) continue;
                    Hoverable h = hit.collider.GetComponentInParent<Hoverable>();
                    if (h == null) continue;
                    Component c = h as Component;
                    if (c != null) return c.gameObject;
                }
                return null;
            }
            catch (Exception e) { Say("laser raycast failed: " + e.Message); return null; }
        }

        private static object Hover(Player p)
        {
            try { return _fHovering == null ? null : _fHovering.GetValue(p); } catch { return null; }
        }

        // The game menu was never reachable in VR: right-stick-click delivers
        // valheim_ToggleMenu (confirmed by ORIGIN=Right Hand oculus_touch Joystick and
        // FIRED valheim_ToggleMenu), but nothing consumed it - VHVR routes it through the
        // ZInput bridge we no longer use, and no direct handler existed. Menu.Show/Hide are
        // public with a public static instance.
        private static void ToggleMenu()
        {
            try
            {
                Type menu = TypeCache.Get("Menu");
                if (menu == null) { Say("Menu type not found"); return; }
                PropertyInfo inst = menu.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                object m = inst == null ? null : inst.GetValue(null, null);
                if (m == null) { Say("Menu.instance null"); return; }
                MethodInfo isVis = AccessTools.Method(menu, "IsVisible");
                bool visible = false;
                try { object r = isVis == null ? null : isVis.Invoke(null, null); visible = r is bool && (bool)r; }
                catch { try { object r = isVis.Invoke(m, null); visible = r is bool && (bool)r; } catch { } }
                MethodInfo target = AccessTools.Method(menu, visible ? "Hide" : "Show");
                if (target == null) { Say("Menu." + (visible ? "Hide" : "Show") + " not found"); return; }
                target.Invoke(target.IsStatic ? null : m, null);
                Say("MENU " + (visible ? "hidden" : "shown"));
            }
            catch (Exception e) { Say("menu toggle failed: " + e.Message); }
        }

        private static void ToggleInventory()
        {
            try
            {
                if (InventoryGui.instance == null) return;
                if (InventoryGui.IsVisible()) InventoryGui.instance.Hide();
                else InventoryGui.instance.Show(null);
                Say("INVENTORY toggled");
            }
            catch (Exception e) { Say("inventory toggle failed: " + e.Message); }
        }

        private static void ToggleMap()
        {
            try
            {
                if (Minimap.instance == null || _mSetMapMode == null) return;
                FieldInfo mode = AccessTools.Field(typeof(Minimap), "m_mode");
                object cur = mode == null ? null : mode.GetValue(Minimap.instance);
                int large = (int)Enum.Parse(typeof(Minimap.MapMode), "Large");
                int small = (int)Enum.Parse(typeof(Minimap.MapMode), "Small");
                int next = Convert.ToInt32(cur) == large ? small : large;
                _mSetMapMode.Invoke(Minimap.instance, new object[] { Enum.ToObject(typeof(Minimap.MapMode), next) });
                Say("MAP mode -> " + next);
            }
            catch (Exception e) { Say("map toggle failed: " + e.Message); }
        }

        private static void Say(string msg)
        {
            if (_logged >= 60) return;
            _logged++;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + msg);
        }
    }

    // Measures the hand speed VHVR's attack system actually observes. Attacks are not
    // buttons: WeaponCollision.hasMomentum and FistCollision.hasMomentum compare
    // PhysicsEstimator.GetVelocity().magnitude against SwingSpeedRequirement (fists at
    // 45% of it). That velocity is derived frame-to-frame, so at 16-26 FPS it may never
    // reach the threshold no matter how hard you swing. This reports the peak actually
    // seen next to the threshold in force, which decides whether the config needs
    // lowering further or the frame rate is the real blocker.
    internal static class SwingWatch
    {
        private static PropertyInfo _rightEst, _leftEst;
        private static float _peakR, _peakL, _nextLog;
        private static bool _failed;
        private static int _logged;
        private static float _threshold = -1f;
        private static bool _missing, _unreadable;

        internal static void Tick()
        {
            if (_failed || _logged >= 30) return;
            try
            {
                if (_rightEst == null)
                {
                    Type vrp = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");
                    if (vrp == null) { _failed = true; return; }
                    _rightEst = vrp.GetProperty("rightHandPhysicsEstimator", BindingFlags.Static | BindingFlags.Public);
                    _leftEst = vrp.GetProperty("leftHandPhysicsEstimator", BindingFlags.Static | BindingFlags.Public);
                    if (_rightEst == null) { _failed = true; return; }
                    Type cfg = TypeCache.Get("ValheimVRMod.Utilities.VHVRConfig");
                    MethodInfo m = cfg == null ? null : cfg.GetMethod("SwingSpeedRequirement", BindingFlags.Static | BindingFlags.Public);
                    if (m != null) _threshold = Convert.ToSingle(m.Invoke(null, null));
                }
                _peakR = Math.Max(_peakR, Speed(_rightEst));
                _peakL = Math.Max(_peakL, Speed(_leftEst));

                if (Time.realtimeSinceStartup < _nextLog) return;
                _nextLog = Time.realtimeSinceStartup + 3f;
                _logged++;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "swing peakR=" + _peakR.ToString("F2") + " peakL=" + _peakL.ToString("F2")
                    + " m/s | threshold=" + _threshold.ToString("F2")
                    + " fistThreshold=" + (_threshold * 0.45f).ToString("F2")
                    + " weaponHit=" + (_peakR >= _threshold) + " fistHit=" + (_peakR > _threshold * 0.45f)
                    + " estimatorMissing=" + _missing + " velocityUnreadable=" + _unreadable);
                _peakR = 0f; _peakL = 0f;
            }
            catch (Exception e)
            {
                _failed = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "swing watch failed: " + e.Message);
            }
        }

        private static float Speed(PropertyInfo prop)
        {
            try
            {
                object est = prop == null ? null : prop.GetValue(null, null);
                if (est == null) { _missing = true; return 0f; }
                // GetVelocity(Vector3? position = null) has ONE parameter. Matching on a
                // zero-argument signature found no method and silently read as 0.00 m/s,
                // which produced a false conclusion that the estimator was dead.
                // GetVelocity(Vector3? position = null): one optional parameter, so the
                // single-null form is the correct call and must be passed as an array.
                object v = SteamVRProbe.Call(est, "GetVelocity", new object[] { null });
                if (!(v is Vector3)) v = SteamVRProbe.Call(est, "GetVelocity");
                if (!(v is Vector3)) { _unreadable = true; return 0f; }
                return ((Vector3)v).magnitude;
            }
            catch { return 0f; }
        }
    }

    // Watches EVERY boolean action the manifest declares, not just the ten VHVR wires to
    // ZInput. Built because the shipped binding provably maps left-stick-click to
    // togglecrouch and right-stick-click to togglemenu, right-stick-click demonstrably
    // fires, and left-stick-click appears not to - which cannot be settled by reading
    // the binding file again. One session with this produces a complete map of physical
    // input to action, including the origin SteamVR actually attached.
    internal static class FullActionWatch
    {
        private static readonly List<KeyValuePair<string, object>> _all = new List<KeyValuePair<string, object>>();
        private static bool _built, _failed;
        private static int _logged;
        private static float _dueAt = -1f;

        private static void Build()
        {
            _built = true;
            try
            {
                Type actions = TypeCache.Get("Valve.VR.SteamVR_Actions");
                if (actions == null) { _failed = true; return; }
                foreach (PropertyInfo prop in actions.GetProperties(BindingFlags.Static | BindingFlags.Public))
                {
                    if (prop.PropertyType.Name != "SteamVR_Action_Boolean") continue;
                    object action = null;
                    try { action = prop.GetValue(null, null); } catch { }
                    if (action == null) continue;
                    _all.Add(new KeyValuePair<string, object>(prop.Name, action));
                }
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "=== full action map (" + _all.Count + " boolean actions) ===");
                foreach (KeyValuePair<string, object> pair in _all)
                {
                    string bound = Convert.ToString(ReadMember(pair.Value, "activeBinding"));
                    string origin = SteamVRProbe.Init() ? SteamVRProbe.Origin(pair.Value) : "<probe unavailable>";
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "  " + pair.Key + " bound=" + bound + " ORIGIN=" + origin);
                }
            }
            catch (Exception e)
            {
                _failed = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "full action map failed: " + e.Message);
            }
        }

        private static object ReadMember(object target, string name)
        {
            try
            {
                PropertyInfo p = target.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public);
                return p == null ? "n/a" : p.GetValue(target, null);
            }
            catch { return "err"; }
        }

        internal static void Tick()
        {
            if (_failed) return;
            if (!_built)
            {
                // Let SteamVR settle its bindings before snapshotting origins.
                if (_dueAt < 0f) { _dueAt = Time.realtimeSinceStartup + 8f; return; }
                if (Time.realtimeSinceStartup < _dueAt) return;
                Build();
                return;
            }
            if (_logged >= 120) return;
            foreach (KeyValuePair<string, object> pair in _all)
            {
                Report(pair, "L", SteamVRProbe.Left);
                Report(pair, "R", SteamVRProbe.Right);
            }
        }

        private static void Report(KeyValuePair<string, object> pair, string hand, object source)
        {
            object down = SteamVRProbe.Call(pair.Value, "GetStateDown", source);
            if (!(down is bool) || !(bool)down) return;
            if (_logged >= 120) return;
            _logged++;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "FIRED " + pair.Key + " hand=" + hand);
        }
    }

    // Reflection bridge to VHVR's internal VRGUI. Mirrors the approach in
    // geekstreet's EpicLootVRFix, which is the only route available: the type is
    // internal and the fields are private, so there is nothing to compile against.
    internal static class VRGuiBridge
    {
        // Names we successfully converted, so DirectActions.ClosePanels has something concrete to
        // deactivate when a mod panel takes the screen and will not give it back.
        private static readonly List<string> _adopted = new List<string>();

        internal static string[] AdoptedNames() { return _adopted.ToArray(); }

        private static Type _vrGuiType;
        private static object _instance;
        private static FieldInfo _guiCanvasesField, _guiCameraField;
        private static bool _resolved;

        private static bool Resolve()
        {
            if (_resolved) return _vrGuiType != null && _instance != null;
            // lint:per-frame bounded - _resolved short-circuits every later call, so the scan for
            // the VRGUI instance happens once per session
            _vrGuiType = TypeCache.Get("ValheimVRMod.VRCore.UI.VRGUI");
            if (_vrGuiType == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "VRGUI type not found; VR fixes inert");
                _resolved = true;
                return false;
            }
            UnityEngine.Object[] found = UnityEngine.Object.FindObjectsOfType(_vrGuiType);
            if (found == null || found.Length == 0) return false; // not up yet, retry later
            _instance = found[0];
            _guiCanvasesField = AccessTools.Field(_vrGuiType, "_guiCanvases");
            _guiCameraField = AccessTools.Field(_vrGuiType, "_guiCamera");
            _resolved = true;
            return true;
        }

        internal static Type VRGuiType
        {
            get { Resolve(); return _vrGuiType; }
        }

        // Converts one named canvas to VHVR's world-space GUI and registers it so
        // VHVR keeps maintaining it. Returns true once the canvas has been handled.
        internal static bool Adopt(string canvasName)
        {
            try
            {
                if (!Resolve()) return false;
                Camera guiCamera = _guiCameraField == null ? null : _guiCameraField.GetValue(_instance) as Camera;
                IList canvases = _guiCanvasesField == null ? null : _guiCanvasesField.GetValue(_instance) as IList;
                if (guiCamera == null || canvases == null) return false;
                foreach (Canvas canvas in Resources.FindObjectsOfTypeAll<Canvas>())
                {
                    if (canvas == null || canvas.name != canvasName) continue;
                    Convert(canvas, guiCamera, canvases);
                    return true;
                }
                return false;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "adopt " + canvasName + " failed: " + e.Message);
                return true; // do not retry a failing name forever
            }
        }

        // One scan for every wanted name. Canvases already in world space are skipped, so this is
        // safe to call on a timer: the cost is one FindObjectsOfTypeAll plus a set lookup per canvas.
        // Called repeatedly rather than once because the companion panels are built on demand.
        internal static int EnsureAdopted(ICollection<string> wanted)
        {
            try
            {
                if (wanted == null || wanted.Count == 0 || !Resolve()) return 0;
                Camera guiCamera = _guiCameraField == null ? null : _guiCameraField.GetValue(_instance) as Camera;
                IList canvases = _guiCanvasesField == null ? null : _guiCanvasesField.GetValue(_instance) as IList;
                if (guiCamera == null || canvases == null) return 0;
                int converted = 0;
                foreach (Canvas canvas in Resources.FindObjectsOfTypeAll<Canvas>())
                {
                    if (canvas == null) continue;
                    if (canvas.renderMode == RenderMode.WorldSpace) continue;   // already ours or VHVR's
                    if (!wanted.Contains(canvas.name)) continue;
                    Convert(canvas, guiCamera, canvases);
                    converted++;
                }
                return converted;
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "ensure-adopt failed: " + e.Message);
                return 0;
            }
        }

        private static void Convert(Canvas canvas, Camera guiCamera, IList canvases)
        {
            // The GUI camera only captures the UI layer, so a canvas on any
            // other layer is never drawn into the panel texture.
            SetLayerRecursive(canvas.gameObject, LayerMask.NameToLayer("UI"));
            string before = Describe(canvas);
            canvas.worldCamera = guiCamera;
            canvas.renderMode = RenderMode.WorldSpace;
            // A screen-space canvas keeps its pixel RectTransform (often 1920x1080 at
            // scale 1). Converted to world space that is a quad hundreds of metres across,
            // so VHVR's GUI camera captures only the part that happens to fall inside its
            // frustum - which is exactly the reported "halfway cut off". Rather than guess
            // at correct numbers, copy the geometry from a canvas VHVR converted itself:
            // those are known to render correctly in this build.
            string matched = MatchGeometryToWorkingCanvas(canvas, canvases);
            if (!canvases.Contains(canvas)) canvases.Add(canvas);
            if (!_adopted.Contains(canvas.name)) _adopted.Add(canvas.name);
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "adopted canvas " + canvas.name
                + " | before " + before + " | after " + Describe(canvas)
                + (matched == null ? " | NO reference canvas to copy from - may render cut off"
                                   : " | geometry copied from " + matched));
        }

        private static string Describe(Canvas canvas)
        {
            RectTransform rt = canvas.transform as RectTransform;
            if (rt == null) return "scale=" + canvas.transform.localScale.x.ToString("F4") + " (no RectTransform)";
            return "size=" + rt.sizeDelta.x.ToString("F0") + "x" + rt.sizeDelta.y.ToString("F0")
                 + " scale=" + rt.localScale.x.ToString("F4")
                 + " pos=" + rt.localPosition.ToString("F2");
        }

        // Finds a canvas already registered with VHVR that is NOT the one being adopted, and copies
        // its RectTransform onto ours. Returns the donor's name, or null if there is nothing to copy.
        private static string MatchGeometryToWorkingCanvas(Canvas target, IList canvases)
        {
            RectTransform mine = target.transform as RectTransform;
            if (mine == null) return null;

            // Pick the LARGEST non-hand canvas as the donor. Two reasons, both learned the hard way:
            //  - copying a donor's parent is what positions the panel, so a wrist-parented donor
            //    would drag the adopted window onto the wrist. A mod config window belongs on the
            //    main GUI panel, so hand/wrist/quick-menu canvases are excluded outright.
            //  - the largest canvas is VHVR's main GUI surface, which is the one known to render
            //    fully rather than clipped.
            RectTransform theirs = null;
            Canvas chosen = null;
            float bestArea = 0f;
            foreach (object item in canvases)
            {
                Canvas donor = item as Canvas;
                if (donor == null || donor == target) continue;
                RectTransform rt = donor.transform as RectTransform;
                if (rt == null || rt.sizeDelta.x <= 0f || rt.sizeDelta.y <= 0f) continue;
                if (IsHandAttached(rt)) continue;
                float area = rt.sizeDelta.x * rt.sizeDelta.y;
                if (area <= bestArea) continue;
                bestArea = area; theirs = rt; chosen = donor;
            }
            if (theirs == null || chosen == null) return null;
            {
                Canvas donor = chosen;
                mine.SetParent(theirs.parent, false);
                mine.sizeDelta = theirs.sizeDelta;
                mine.localScale = theirs.localScale;
                mine.localPosition = theirs.localPosition;
                mine.localRotation = theirs.localRotation;
                mine.anchorMin = theirs.anchorMin;
                mine.anchorMax = theirs.anchorMax;
                mine.pivot = theirs.pivot;
                target.referencePixelsPerUnit = donor.referencePixelsPerUnit;
                return donor.name + " (" + theirs.sizeDelta.x.ToString("F0") + "x" + theirs.sizeDelta.y.ToString("F0") + ")";
            }
        }

        // True if any ancestor looks like a hand, wrist or quick-menu object. Name-based because the
        // VHVR types involved are internal and the set of hand-parented canvases is not fixed.
        private static bool IsHandAttached(Transform t)
        {
            for (Transform cur = t; cur != null; cur = cur.parent)
            {
                string n = cur.name;
                if (string.IsNullOrEmpty(n)) continue;
                n = n.ToLowerInvariant();
                if (n.Contains("wrist") || n.Contains("hand") || n.Contains("quickmenu")
                    || n.Contains("quickaction") || n.Contains("controller")) return true;
            }
            return false;
        }

        // VHVR's own conversion sets worldCamera and renderMode but leaves the layer
        // alone, and its GUI camera renders only the UI layer. Any canvas it adopted
        // whose objects sit on a different layer is therefore captured by nothing and
        // simply does not appear. Sweeping every canvas VHVR knows about fixes that
        // class of defect for mods we have not individually identified.
        internal static void SweepAdoptedCanvasLayers()
        {
            try
            {
                if (!Resolve()) return;
                IList canvases = _guiCanvasesField == null ? null : _guiCanvasesField.GetValue(_instance) as IList;
                if (canvases == null) return;
                int uiLayer = LayerMask.NameToLayer("UI");
                if (uiLayer < 0) return;
                int moved = 0;
                foreach (object item in canvases)
                {
                    Canvas canvas = item as Canvas;
                    if (canvas == null || canvas.gameObject.layer == uiLayer) continue;
                    SetLayerRecursive(canvas.gameObject, uiLayer);
                    moved++;
                }
                if (moved > 0)
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "moved " + moved + " adopted canvas(es) onto the UI layer");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "layer sweep failed: " + e.Message);
            }
        }

        private static void SetLayerRecursive(GameObject go, int layer)
        {
            if (go == null || layer < 0) return;
            go.layer = layer;
            foreach (Transform child in go.transform) SetLayerRecursive(child.gameObject, layer);
        }
    }

    // Fix 1: collapse the per-camera panel update to one per frame.
    internal static class PanelFrameGuard
    {
        private static int _lastFrame = -1;

        internal static void Install(Harmony harmony)
        {
            if (!NeuralyzeVRFixesPlugin.FramePanelGuard.Value) return;
            try
            {
                Type vrGui = VRGuiBridge.VRGuiType;
                MethodInfo target = vrGui == null ? null : AccessTools.Method(vrGui, "updateUiPanelScaleAndPosition");
                if (target == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "updateUiPanelScaleAndPosition not found; panel guard inert");
                    return;
                }
                harmony.Patch(target, prefix: new HarmonyMethod(typeof(PanelFrameGuard).GetMethod(nameof(Prefix), BindingFlags.Static | BindingFlags.NonPublic)));
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "panel frame guard installed");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "panel guard install failed: " + e.Message);
            }
        }

        // Returning false skips the original. The first call each frame is allowed
        // through, so the panel is still placed every frame - just once, from one
        // camera's invocation, which is what the interpolation maths assumes.
        private static bool Prefix()
        {
            int frame = Time.frameCount;
            if (frame == _lastFrame) return false;
            _lastFrame = frame;
            return true;
        }
    }

    // Fix 4: strip keyboard key hints from the hover text.
    //
    // Vanilla builds the hover label as the object name plus a prompt line naming a
    // keyboard key, e.g. "Branch\n[E] Pick up". In VR that key does not exist - the action
    // is a SteamVR boolean binding - so the prompt is actively misleading. Whole lines
    // containing a bracketed hint are dropped, which removes the prompt and keeps the
    // object name. Reflection is used for the text field because it is a TextMeshPro type
    // and this assembly is not compiled against TMPro.
    [HarmonyPatch(typeof(Hud), "UpdateCrosshair")]
    internal static class StripHoverKeyHints
    {
        private static FieldInfo _hoverName;
        private static PropertyInfo _text;
        private static bool _resolved;

        private static bool Prepare() { return true; }

        private static void Postfix(Hud __instance)
        {
            try
            {
                if (!_resolved)
                {
                    _resolved = true;
                    _hoverName = AccessTools.Field(typeof(Hud), "m_hoverName");
                }
                if (_hoverName == null) return;
                object label = _hoverName.GetValue(__instance);
                if (label == null) return;
                if (_text == null)
                {
                    _text = label.GetType().GetProperty("text", BindingFlags.Instance | BindingFlags.Public);
                    if (_text == null) return;
                }
                string current = _text.GetValue(label, null) as string;

                // The contextual menu takes over this label while it is open.
                string menu = HoverMenu.HoverText();
                if (menu != null)
                {
                    _text.SetValue(label, menu, null);
                    return;
                }

                if (!NeuralyzeVRFixesPlugin.SuppressKeyHints.Value) return;
                if (string.IsNullOrEmpty(current) || current.IndexOf('[') < 0) return;
                string cleaned = StripHintLines(current);
                if (cleaned != current) _text.SetValue(label, cleaned, null);
            }
            catch { }
        }

        // Drops any line carrying a bracketed key prompt, preserving the rest verbatim.
        internal static string StripHintLines(string value)
        {
            string[] lines = value.Split('\n');
            var kept = new List<string>(lines.Length);
            foreach (string line in lines)
            {
                int open = line.IndexOf('[');
                if (open >= 0 && line.IndexOf(']', open) > open) continue;
                kept.Add(line);
            }
            if (kept.Count == 0) return "";
            return string.Join("\n", kept.ToArray()).TrimEnd();
        }
    }

    // Fix 3: keep the quick-slot bar hidden on this client regardless of the
    // server-synced visibility setting. Runs after AzuEPI has positioned the bar.
    [HarmonyPatch]
    internal static class HideQuickSlots
    {
        private static MethodBase TargetMethod()
        {
            return AccessTools.Method("AzuEPI.Game.Slots.QAB.QuickAccessBar:SetElementPositions");
        }

        private static bool Prepare()
        {
            return NeuralyzeVRFixesPlugin.HideQuickSlotBar.Value
                && AccessTools.Method("AzuEPI.Game.Slots.QAB.QuickAccessBar:SetElementPositions") != null;
        }

        private static void Postfix()
        {
            try
            {
                if (Hud.instance == null) return;
                Transform root = Hud.instance.transform.Find("hudroot");
                Transform bar = root == null ? null : root.Find("AzuEPI_QuickAccessBar");
                if (bar != null && bar.gameObject.activeSelf) bar.gameObject.SetActive(false);
            }
            catch { }
        }
    }
}
