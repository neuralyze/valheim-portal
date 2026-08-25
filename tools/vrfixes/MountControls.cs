using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Speed control for anything you sit on, through the game's own path.
    //
    // VHVR deletes the keyboard route for vehicle speed - "Forward" and "Backward" are in its
    // ignoredZInputs - and replaces it with a two-handed sail-pull gesture. That gesture cannot be
    // performed while one hand steers, it is undiscoverable, and it does not exist at all for a
    // horse. The result was a player on a raft who could turn but never move.
    //
    // Rather than drive each vehicle's API separately, this answers the two buttons the game already
    // reads. PlayerController.FixedUpdate reads GetButton("Forward"/"Backward") and feeds whatever
    // you are attached to, so one answer covers ships and saddles, and every mod vehicle built on
    // them, using vanilla logic rather than a parallel implementation.
    //
    // Safe against a held stick: Ship.ApplyControlls only steps when its own m_forwardPressed flag
    // is not already set, so holding the stick up raises the sail exactly one step, not ten.
    //
    // The right stick is free while seated - VHVR reads its Y axis only in build mode
    // (getPieceRefModifier and the context scroll, both gated on InPlaceMode) - which is why this
    // does not need a chord or a modifier.
    internal static class MountControls
    {
        private const float Deflection = 0.5f;

        private static MethodInfo _stickY;
        private static MethodInfo _stickX;
        private static MethodInfo _leftStickX;
        private static MethodInfo _leftStickY;
        private static PropertyInfo _vrControls;
        private static MethodInfo _isAttached;
        private static FieldInfo _localPlayer;
        private static FieldInfo _doodadField;
        private static bool _resolved;

        private static int _frame = -1;
        private static float _cachedY;
        private static float _cachedApiLeftX, _cachedApiLeftY, _cachedApiRightX, _cachedApiRightY;
        private static bool _cachedAttached;

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            try
            {
                Type vr = TypeCache.Get("ValheimVRMod.VRCore.UI.VRControls");
                if (vr != null)
                {
                    _vrControls = vr.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    _stickY = AccessTools.Method(vr, "GetJoyRightStickY");
                    _stickX = AccessTools.Method(vr, "GetJoyRightStickX");
                    _leftStickX = AccessTools.Method(vr, "GetJoyLeftStickX");
                    _leftStickY = AccessTools.Method(vr, "GetJoyLeftStickY");
                }
                Type character = TypeCache.Get("Character");
                Type player = TypeCache.Get("Player");
                _isAttached = character == null ? null : AccessTools.Method(character, "IsAttached");
                _localPlayer = player == null ? null : AccessTools.Field(player, "m_localPlayer");
                _doodadField = player == null ? null : AccessTools.Field(player, "m_doodadController");

                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "mount controls: stickY=" + (_stickY != null) + " attachedProbe=" + (_isAttached != null)
                    + " - right stick Y steps vehicle speed while seated");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "mount controls unavailable: " + e.Message);
            }
        }

        // Both reads are cached per frame: this is consulted from a button postfix that the game
        // calls dozens of times per frame, and uncached reflection on that path is exactly what cost
        // the player their frame rate twice already.
        private static void Sample()
        {
            if (_frame == Time.frameCount) return;
            _frame = Time.frameCount;
            Resolve();

            _cachedAttached = false;
            _cachedY = 0f;
            _cachedApiLeftX = _cachedApiLeftY = _cachedApiRightX = _cachedApiRightY = 0f;
            try
            {
                object p = _localPlayer == null ? null : _localPlayer.GetValue(null);
                if (p == null) return;
                object doodad = _doodadField == null ? null : _doodadField.GetValue(p);
                bool attached = _isAttached != null && Convert.ToBoolean(_isAttached.Invoke(p, null));
                _cachedAttached = doodad != null || attached;
                if (!_cachedAttached) return;   // the stick is only ours while seated

                object controls = _vrControls == null ? null : _vrControls.GetValue(null, null);
                if (controls == null || _stickY == null) return;
                _cachedY = Convert.ToSingle(_stickY.Invoke(controls, null));
                if (_leftStickX != null) _cachedApiLeftX = Convert.ToSingle(_leftStickX.Invoke(controls, null));
                if (_leftStickY != null) _cachedApiLeftY = Convert.ToSingle(_leftStickY.Invoke(controls, null));
                if (_stickX != null) _cachedApiRightX = Convert.ToSingle(_stickX.Invoke(controls, null));
                if (_stickY != null) _cachedApiRightY = Convert.ToSingle(_stickY.Invoke(controls, null));
            }
            catch { }
        }

        internal static bool Attached()
        {
            Sample();
            return _cachedAttached;
        }

        // +1 push forward, -1 pull back, 0 centred.
        // The left stick's X, which is free while riding: the legs are not walking.

        internal static float ApiLeftX()  { Sample(); return _cachedApiLeftX; }
        internal static float ApiLeftY()  { Sample(); return _cachedApiLeftY; }
        internal static float ApiRightX() { Sample(); return _cachedApiRightX; }
        internal static float ApiRightY() { Sample(); return _cachedApiRightY; }

        // The right stick's Y axis, read whether or not the player is seated, for the hover menu.
        //
        // Sample() above zeroes every channel and returns at :95 unless attached, on purpose: while
        // seated the stick belongs to mount steering and to nothing else, and Step() must never see
        // a deflection that came from a player on foot. Loosening that gate would feed walking input
        // into vehicle speed, which is the class of steering bug that already cost days. So this is a
        // separate read that shares nothing with the seated caches: the same VHVR source resolved at
        // :55 (GetJoyRightStickY), its own one-read-per-frame cache, and no attachment test. It is
        // safe because it only reports the axis - no button answer is derived from it - and its only
        // caller consults it while the hover list is open, when the stick has no other job.
        private static int _rawFrame = -1;
        private static float _rawRightY;

        internal static float RawRightStickY()
        {
            if (_rawFrame == Time.frameCount) return _rawRightY;
            _rawFrame = Time.frameCount;
            _rawRightY = 0f;
            Resolve();
            try
            {
                object controls = _vrControls == null ? null : _vrControls.GetValue(null, null);
                if (controls == null || _stickY == null) return _rawRightY;
                _rawRightY = Convert.ToSingle(_stickY.Invoke(controls, null));
            }
            catch { }
            return _rawRightY;
        }

        // The DRIVE stick's sideways axis. Established by the calibration ride: pushing the left
        // stick down stopped the horse and showed up as apiLeft, so that channel is the rider's
        // left hand, and it is also the one the game takes speed from. One stick, both jobs.

        // Both pairs, for the log line.

        internal static int Step()
        {
            Sample();
            if (!_cachedAttached) return 0;
            if (_cachedY > Deflection) return 1;
            if (_cachedY < -Deflection) return -1;
            return 0;
        }

        private static int _lastLogged;

        // On the EDGE of a stick push, act directly as well as answering the button.
        //
        // Answering GetButton("Forward") demonstrably reached the game - the log recorded every
        // push - and the ship still did not change speed, so the consumer of that answer is not
        // doing what the bytecode suggested. Ship.Forward()/Backward() are a single RPC each with no
        // gate at all, so calling them is verifiable in a way that emulating a keypress is not.
        // The button answer stays for saddles, which have no equivalent entry point.
        internal static void Note(int step)
        {
            if (step == _lastLogged) return;
            int previous = _lastLogged;
            _lastLogged = step;
            if (step == 0 || previous != 0) return;

            string before = ShipSpeedNow();
            DirectActions.ShipSpeed(step > 0 ? "faster" : "slower");
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "mount speed " + (step > 0 ? "forward" : "back") + " from the right stick: "
                + before + " -> " + ShipSpeedNow());
        }

        // The ship's own speed enum, so a push that does nothing is visible as a push that did
        // nothing rather than as a push that was never seen.
        private static string ShipSpeedNow()
        {
            try
            {
                Type player = TypeCache.Get("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) return "?";
                FieldInfo doodadField = AccessTools.Field(player, "m_doodadController");
                object doodad = doodadField == null ? null : doodadField.GetValue(local);
                if (doodad == null) return "not-at-a-helm";
                FieldInfo shipField = AccessTools.Field(doodad.GetType(), "m_ship");
                object ship = shipField == null ? null : shipField.GetValue(doodad);
                if (ship == null) return "no-ship";
                FieldInfo speed = AccessTools.Field(ship.GetType(), "m_speed");
                return speed == null ? "?" : Convert.ToString(speed.GetValue(ship));
            }
            catch { return "?"; }
        }
    }

    // Drop or raise the anchor from a grip, while your hands stay on the helm.
    //
    // Reported as: "is there already or can we add the control for drop/raise anchor on boat
    // controls? so when you are sitting and you can use your controller up/down/left/right
    // thumbstick for controlling boat, can we add one mapping to grip which drops/raises anchor
    // rather than having to look and use hover menu?"
    //
    // The anchor is NOT the game's. monodis assembly_valheim.dll has no anchor on Ship at all; it
    // belongs to BoatAdditions, whose config says so in its own words: "Hotkey combination to
    // raise or drop anchors while hovering your mouse to the rudder", Anchor Key = F + LeftShift.
    // So this reuses that hotkey rather than inventing an action, exactly as the shipped hover ring
    // does with 'ship: Anchor=key:LeftShift+F'.
    //
    // Pulsing the chord is necessary and NOT sufficient, and the mod's own IL says why. Disassembled
    // (monodis BoatAdditions.dll), the hotkey is read in exactly one place - inside
    // BoatAdditions.Functions.Anchor::GetHoverText:
    //   IL_0000  ldsfld  Plugin::_anchorKey  ...  KeyboardShortcut::IsDown()
    //   IL_0012  brfalse.s IL_004b
    //   IL_0014..IL_0046  m_nview.InvokeRPC("UseAnchor", localPlayer ZDOID UserID)
    // GetHoverText is a Hoverable member: the game calls it only for the object in
    // Player::m_hovering. Seated at the helm nothing is hovered, so the pulse would land in a frame
    // where nobody reads it. The hover ring works today only because pointing at the ship IS what
    // sets m_hovering. So this pulses the chord AND calls that same reader once, in the same frame -
    // the mod's own entry point, not a reimplementation of its RPC.
    //
    // It is skipped when the ship is already hovered, because then the game will call GetHoverText
    // itself this frame and two reads of one IsDown() would fire the toggle twice, i.e. do nothing.
    internal static class ShipAnchor
    {
        private static bool _armed;          // a grip already seen released while at this helm
        private static bool _held;
        private static object _ship, _anchor;
        private static MethodInfo _hoverText, _getZdo, _getBool;
        private static FieldInfo _nview, _keyHash, _hovering, _localPlayer;
        private static bool _refs;
        private static bool _dead;

        private static void Refs()
        {
            if (_refs) return;
            _refs = true;
            Type player = TypeCache.Get("Player");
            if (player != null)
            {
                _localPlayer = AccessTools.Field(player, "m_localPlayer");
                _hovering = AccessTools.Field(player, "m_hovering");
            }
        }

        internal static void Tick()
        {
            if (_dead) return;
            if (NeuralyzeVRFixesPlugin.AnchorGrip == null) return;
            string hand = NeuralyzeVRFixesPlugin.AnchorGrip.Value;
            if (hand == null || hand.Equals("Off", StringComparison.OrdinalIgnoreCase)) return;
            try
            {
                // Cheapest gate first: AtHelm() is two cached field reads, and the reflective grip
                // probe below is therefore never asked while the player is on foot.
                object ship = DirectActions.AtHelm() ? DirectActions.SteeredShip() : null;
                if (ship == null)
                {
                    // Off a helm, or on a saddle rather than a ship. Disarm, so that walking up to
                    // a rudder with the grip already squeezed cannot fire on the first frame: the
                    // grip has to be seen RELEASED at the helm before a press counts.
                    _armed = false;
                    _held = false;
                    _ship = null;
                    _anchor = null;
                    return;
                }

                bool now = hand.Equals("Right", StringComparison.OrdinalIgnoreCase)
                    ? DirectActionInvoker.RightGrabHeld()
                    : DirectActionInvoker.LeftGrabHeld();
                bool wasHeld = _held;
                _held = now;
                if (!now) { _armed = true; return; }
                if (!_armed || wasHeld) return;    // held, not pressed: fires once per squeeze

                // The hover ring owns the right grip while it is up (Modifier=RightGrip), and both
                // grips together are VHVR's sail pull and rowing gesture (ShipSteering.cs:75-110).
                // Neither is a frame to also toggle the anchor in.
                if (HoverMenu.MenuOpen) return;
                if (DirectActionInvoker.LeftGrabHeld() && DirectActionInvoker.RightGrabHeld()) return;

                Fire(ship);
            }
            catch (Exception e)
            {
                // Loud once, then silent - and it says it has stopped. A control that dies quietly
                // is the failure this session was sent to investigate, so it is not repeated here.
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "grip anchor DISABLED for this session after: " + e.Message);
            }
        }

        private static void Fire(object ship)
        {
            Refs();
            if (!ReferenceEquals(ship, _ship))
            {
                _ship = ship;
                _anchor = FindAnchor(ship);
            }

            string before = Anchored();
            string chord = NeuralyzeVRFixesPlugin.AnchorKey == null ? "LeftShift+F" : NeuralyzeVRFixesPlugin.AnchorKey.Value;
            bool sent = KeyPulse.Send(chord);

            string drove;
            if (_anchor == null)
            {
                drove = "NO ANCHOR COMPONENT on this ship - a vanilla hull has none, only a modded boat does";
            }
            else if (_hoverText == null)
            {
                drove = "no GetHoverText on " + _anchor.GetType().FullName + " - chord pulsed blind";
            }
            else if (HoveringShip(ship))
            {
                drove = "skipped - the game is reading this ship's hover text itself this frame";
            }
            else
            {
                _hoverText.Invoke(_anchor, null);
                drove = "yes";
            }

            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                + "grip anchor: chord '" + chord + "' " + (sent ? "pulsed" : "REJECTED (unknown KeyCode)")
                + ", reader driven=" + drove
                + ", anchored " + before + " -> " + Anchored()
                + " (a remote-owned ship answers its RPC a frame or two later, so an unchanged"
                + " reading here is not a refusal)");
        }

        // Any component on the ship called "Anchor". Matched by name rather than by the mod's type,
        // so a different anchor mod is found too, and resolved once per ship rather than per frame.
        private static object FindAnchor(object ship)
        {
            _hoverText = null;
            _nview = null;
            _keyHash = null;
            Component shipComponent = ship as Component;
            if (shipComponent == null) return null;
            // lint:per-frame bounded by the grip rising edge in Tick and the ship-identity check in
            // Fire - this runs at most once per ship boarded, never on a frame where nothing changed.
            // The gate reaches it statically through LateUpdate -> Tick -> Fire and cannot see either
            // guard, so the bound is stated here rather than the scan being made lazier than it is.
            // Kept as one scan on purpose: the alternative, resolving on attach, would need a hook on
            // the attach path for a component that only matters the moment a grip is pressed.
            MonoBehaviour[] all = shipComponent.GetComponentsInChildren<MonoBehaviour>(true);
            foreach (MonoBehaviour mb in all)
            {
                if (mb == null) continue;
                Type t = mb.GetType();
                if (t.Name != "Anchor") continue;
                _hoverText = AccessTools.Method(t, "GetHoverText");
                _nview = AccessTools.Field(t, "m_nview");
                // A ZDO key hash, held in a private static int - BoatAdditions calls it
                // m_isAnchored and reads it as ZDO.GetBool(m_isAnchored, false).
                _keyHash = AccessTools.Field(t, "m_isAnchored");
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "grip anchor bound to " + t.FullName + " on " + shipComponent.name
                    + " (hoverText=" + (_hoverText != null) + " state=" + (_nview != null && _keyHash != null) + ")");
                return mb;
            }
            return null;
        }

        private static bool HoveringShip(object ship)
        {
            try
            {
                if (_localPlayer == null || _hovering == null) return false;
                object p = _localPlayer.GetValue(null);
                if (p == null) return false;
                GameObject hovered = _hovering.GetValue(p) as GameObject;
                Component shipComponent = ship as Component;
                if (hovered == null || shipComponent == null) return false;
                return hovered.transform.IsChildOf(shipComponent.transform);
            }
            catch { return false; }
        }

        // "yes"/"no" from the mod's own ZDO flag, so the log names the resulting state rather than
        // asserting that a keypress was sent.
        private static string Anchored()
        {
            try
            {
                if (_anchor == null || _nview == null || _keyHash == null) return "?";
                object nview = _nview.GetValue(_anchor);
                if (nview == null) return "?";
                if (_getZdo == null) _getZdo = AccessTools.Method(nview.GetType(), "GetZDO");
                object zdo = _getZdo == null ? null : _getZdo.Invoke(nview, null);
                if (zdo == null) return "?";
                if (_getBool == null)
                {
                    _getBool = AccessTools.Method(zdo.GetType(), "GetBool", new Type[] { typeof(int), typeof(bool) });
                }
                if (_getBool == null) return "?";
                object r = _getBool.Invoke(zdo, new object[] { Convert.ToInt32(_keyHash.GetValue(null)), false });
                return Convert.ToBoolean(r) ? "yes" : "no";
            }
            catch { return "?"; }
        }
    }

    // Fault 1: forward and backward are unreachable at a helm, and the controls feel like they
    // rotate under you. Reported as "i cant see the direction wheel or boat speed indicators
    // anymore in hud, boat seems to randomly change controls, could turn then couldnt turn".
    //
    // Measured from the operator's own session (LogOutput.log, 2026-08-25, 41 helm watch lines),
    // three consecutive samples seconds before the helm went away:
    //   vhvrStick=(0.35,-0.09) gameStick=(0.35,0.00) rudder=0.00 speed=Stop headOffHeading=-28deg
    //   vhvrStick=(0.64,-0.76) gameStick=(0.64,0.00) rudder=0.00 speed=Stop headOffHeading=-31deg
    //   vhvrStick=(-0.74,0.67) gameStick=(-0.74,0.00) rudder=0.00 speed=Stop headOffHeading=-31deg
    // X passed through and Y was zeroed on EVERY sample, so the sail could never leave Stop.
    //
    // VHVR's ship deadzone is 0.3 on X and 0.9 on Y (ControlPatches.cs:103 and :128, whose own
    // comment is "add deadzone to ship control for forward and backward"). His |Y| peaked at 0.76,
    // so 0.9 was unreachable - and the reason is UPSTREAM of the deadzone, not in it.
    //
    // VRControls.GetJoyLeftStickInput (VRControls.cs:614-639) does not scale the stick, it ROTATES
    // it. With look locomotion on it builds an orthonormal basis from the configured joystick
    // forward - heading, then right = cross(up, heading) - forms velocity = right*ax + heading*ay,
    // and projects that onto playerTransform.right/forward. Both bases are orthonormal and span the
    // same horizontal plane, so the MAGNITUDE is preserved exactly; what changes is how that
    // magnitude is split between the two axes. Substituting heading = forward*cos(phi) +
    // right*sin(phi), with phi the signed yaw from the player's forward to that heading:
    //     out.x =  ax*cos(phi) - u*sin(phi)
    //     out.y =  ax*sin(phi) + u*cos(phi)      where u = -ay, VHVR's own sign for this axis
    // i.e. a plain rotation of (ax, u) by phi. At phi = 0 that degenerates to (ax, -ay), which is
    // exactly what VHVR's no-look-locomotion branch returns (VRControls.cs:623-625) - the check
    // that this is the same rotation and not an approximation of it.
    //
    // So a full forward push arrives as |y| = cos(phi), and Player::UpdateAttach pins the body's
    // rotation to the helm's every frame (av.il:68845-68855), which makes phi the head-off-heading
    // angle the watch already records. acos(0.9) is 25.8 degrees: past 26 degrees off the boat's
    // heading a 0.9 gate on Y cannot be passed even at full deflection. His samples were 28-31
    // degrees with a peak |y| of 0.76, i.e. about 40 degrees.
    //
    // ANSWER to "is the deadzone the symptom or the cause": the rotation is the cause and the 0.9
    // number is what converts it into "no forward or back at all". Both are real, so lowering the
    // number alone would have been treating the symptom - it would have left the controls
    // head-relative, which is the other half of what he reported.
    //
    // The fix therefore removes the rotation where it has no meaning. While the player holds a
    // VEHICLE's controls the stick is read RAW, from the same SteamVR action VHVR reads
    // (SteamVR_Actions.valheim_Walk, bound at VRControls.cs:891), and no rotation is applied at
    // all: a helm is boat-relative by construction, because the body is bolted to it. The deadzone
    // survives, because its purpose is real - a resting thumbstick must not creep the ship - and it
    // is now applied to a number the player can actually reach.
    //
    // Scoped to a held DOODAD rather than to IsAttached(), which is what VHVR gates on. A chair, a
    // bed and an emote are all IsAttached() and none of them steers anything, so narrowing to
    // m_doodadController leaves every one of them exactly as VHVR has it and makes this patch inert
    // everywhere except a helm or a saddle.
    internal static class ShipStick
    {
        // VHVR's own turn band, kept as it is: X was never the broken axis, and matching the number
        // means a player who has learned the boat's dead zone does not have to relearn it.
        private const float TurnDeadzone = 0.3f;

        private static bool _resolved, _announced;
        private static object _walk;
        private static PropertyInfo _axisProp, _mainActive;
        private static FieldInfo _localPlayer, _doodadField;

        private static int _frame = -1;
        private static bool _live;
        private static float _x, _y;
        private static Vector2 _raw;

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            try
            {
                Type player = TypeCache.Get("Player");
                if (player != null)
                {
                    _localPlayer = AccessTools.Field(player, "m_localPlayer");
                    _doodadField = AccessTools.Field(player, "m_doodadController");
                }
                Type vr = TypeCache.Get("ValheimVRMod.VRCore.UI.VRControls");
                if (vr != null) _mainActive = vr.GetProperty("mainControlsActive", BindingFlags.Static | BindingFlags.Public);

                // The action, not VRControls: VRControls.walk is private, and going through its
                // public readers is precisely what applies the rotation we are here to remove.
                // SteamVR_Actions is where VHVR itself gets the action from (VRControls.cs:891).
                if (SteamVRProbe.Init())
                {
                    Type actions = TypeCache.Get("Valve.VR.SteamVR_Actions");
                    PropertyInfo p = actions == null
                        ? null : actions.GetProperty("valheim_Walk", BindingFlags.Static | BindingFlags.Public);
                    _walk = p == null ? null : p.GetValue(null, null);
                    if (_walk != null)
                    {
                        _axisProp = _walk.GetType().GetProperty("axis", BindingFlags.Instance | BindingFlags.Public);
                    }
                }
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "boat-relative stick unavailable, VHVR's rotated stick stands: " + e.Message);
            }
        }

        // The true thumbstick, before any rotation. GetAxis(Any) is what VHVR's walk.axis resolves
        // to, so this is the same number it starts from - read per frame and cached, because the
        // postfixes below sit on a method the game calls several times a frame and uncached
        // reflection on the input path has cost this client its frame rate twice already.
        private static bool ReadRaw(out Vector2 axis)
        {
            axis = Vector2.zero;
            if (_walk == null) return false;
            object r = SteamVRProbe.Call(_walk, "GetAxis", SteamVRProbe.Any);
            if (!(r is Vector2) && _axisProp != null)
            {
                try { r = _axisProp.GetValue(_walk, null); } catch { r = null; }
            }
            if (!(r is Vector2)) return false;
            axis = (Vector2)r;
            return true;
        }

        private static void Sample()
        {
            if (_frame == Time.frameCount) return;
            _frame = Time.frameCount;
            _live = false;
            _x = _y = 0f;
            _raw = Vector2.zero;
            if (NeuralyzeVRFixesPlugin.BoatRelativeStick == null
                || !NeuralyzeVRFixesPlugin.BoatRelativeStick.Value) return;
            Resolve();
            try
            {
                // VHVR's own precondition. Its patches do nothing unless main controls are active,
                // and GetJoyLeftStickInput returns Vector2.zero unless the action set is live
                // (VRControls.cs:616-619); answering with a raw stick outside that window would be
                // feeding input the rest of the mod believes is switched off.
                if (_mainActive == null || !Convert.ToBoolean(_mainActive.GetValue(null, null))) return;
                object p = _localPlayer == null ? null : _localPlayer.GetValue(null);
                if (p == null || _doodadField == null) return;
                if (_doodadField.GetValue(p) == null) return;    // vehicles only, never a chair
                if (!ReadRaw(out _raw)) return;

                float band = NeuralyzeVRFixesPlugin.ShipSpeedDeadzone == null
                    ? 0.5f : NeuralyzeVRFixesPlugin.ShipSpeedDeadzone.Value;
                _x = Mathf.Abs(_raw.x) < TurnDeadzone ? 0f : _raw.x;
                // Negated to match VHVR's convention for this axis (VRControls.cs:624), which the
                // game then negates again - PlayerController::FixedUpdate does
                // "moveDir.z += -GetJoyLeftStickY(true)" (av.il:72581-72589) - so stick-up ends up
                // as +z, and Ship::ApplyControlls takes z > 0.5 as Forward (av.il:416294-416305).
                _y = Mathf.Abs(_raw.y) < band ? 0f : -_raw.y;
                _live = true;
                // Once, guarded by our own flag rather than by ProbeHealth's HashSet: Announce
                // builds its detail string at the CALL site, so leaving the guard to it would
                // allocate three strings every frame the boat is under way. That is the exact class
                // of per-frame cost this plugin has already paid for twice.
                if (!_announced)
                {
                    _announced = true;
                    ProbeHealth.Announce("ShipStick", true,
                        "vehicle stick read raw, deadzone X " + TurnDeadzone.ToString("F2")
                        + " Y " + band.ToString("F2") + " - VHVR's look-locomotion rotation bypassed at a helm");
                }
            }
            catch { _live = false; }
        }

        internal static bool Live()   { Sample(); return _live; }
        internal static float X()     { Sample(); return _x; }
        internal static float Y()     { Sample(); return _y; }
        internal static Vector2 Raw() { Sample(); return _raw; }
    }

    // Both axes, replaced rather than added to.
    //
    // PATCH ORDER. VHVR postfixes the same two methods (ControlPatches.cs:95-142) and, while
    // IsAttached(), REPLACES __result with its own value - either 0f inside the deadzone or
    // "__result += joystick" outside it. So composing with it is meaningless: whatever it wrote is
    // the rotated number we are here to discard. Priority.Last plus HarmonyAfter on VHVR's HARMONY
    // id - "com.valheimvrmod.patches" (HarmonyPatcher.cs:7), which is NOT its BepInPlugin GUID
    // "org.bepinex.plugins.valheimvrmod" - puts this after it, and the write is a plain assignment
    // so the composed result is ours alone.
    //
    // Composed, for the two readings the ticket asks about, at the operator's measured 31 degrees
    // off heading and with the stick pushed straight forward (raw = (0, s)):
    //   s = 0.50 - VHVR computes rotated y = -0.50*cos(31) = -0.43, |0.43| < 0.9 so it writes
    //     __result = 0f: no speed change, which is what he lived with. This postfix then reads raw
    //     y = 0.50, |0.50| >= the 0.5 band, and writes __result = -0.50, so moveDir.z = +0.50.
    //     Ship::ApplyControlls needs z > 0.5 STRICTLY (av.il:416294-416298), so 0.50 exactly is
    //     still no step - the band is the floor for being heard, not for acting, and half
    //     deflection is deliberately the edge.
    //   s = 0.95 - VHVR computes rotated y = -0.95*cos(31) = -0.81, |0.81| < 0.9, __result = 0f
    //     again: still nothing, at 95% deflection. This postfix writes __result = -0.95, so
    //     moveDir.z = +0.95 > 0.5 and the sail steps up exactly once, because Ship::ApplyControlls
    //     will not step again while m_forwardPressed is already set (av.il:416306-416314).
    // If the order ever inverted and VHVR ran last, the outcome is its current behaviour - broken,
    // but no worse than today - which is why this replaces rather than accumulates.
    // One class per target, matching the rest of this plugin: a class-level [HarmonyPatch] with
    // only per-method targets relies on Harmony's auxiliary-method resolution, and this path can be
    // verified only in a headset, so it uses the shape every other patch here already proves.
    [HarmonyPatch(typeof(ZInput), "GetJoyLeftStickX", new Type[] { typeof(bool) })]
    internal static class ShipStick_TurnAxis
    {
        [HarmonyPriority(Priority.Last)]
        [HarmonyAfter("com.valheimvrmod.patches")]
        private static void Postfix(ref float __result)
        {
            if (!ShipStick.Live()) return;
            __result = ShipStick.X();
        }
    }

    [HarmonyPatch(typeof(ZInput), "GetJoyLeftStickY", new Type[] { typeof(bool) })]
    internal static class ShipStick_SpeedAxis
    {
        [HarmonyPriority(Priority.Last)]
        [HarmonyAfter("com.valheimvrmod.patches")]
        private static void Postfix(ref float __result)
        {
            if (!ShipStick.Live()) return;
            __result = ShipStick.Y();
        }
    }

    // Fault 3: name the release path, because none of them says a word.
    //
    // The 2026-08-25 log said "helm LOST after 36.6s" and could not say why. There are two distinct
    // exits from a doodad and only ONE of them passes through Player::StopDoodadControl:
    //   - the game's own releases - Player::Update use-with-nothing-hovered, Player::SetControls
    //     jump/attack/secondaryAttack/dodge, and Player::UpdateDoodadControls for both the distance
    //     check and !IsValid()
    //   - VHVR's MountedAttackUtils.UnmountIfJumping, which nulls Player.m_doodadController itself
    //     (MountedAttackUtils.cs:26) and never calls StopDoodadControl at all
    // Hooking only the first would have missed the second entirely - and the second is the one VHVR
    // substituted into the two most likely release sites - so both are hooked.
    //
    // The caller is read from the stack rather than guessed. Vanilla has three release sites, so
    // the immediate frame is the whole answer: Player.Update, Player.SetControls or
    // Player.UpdateDoodadControls. A StackTrace is expensive and that is fine: it runs only on an
    // actual release, at most twelve times a session and never twice within half a second.
    internal static class DoodadRelease
    {
        private const int MaxReports = 12;
        private const float MinGapSeconds = 0.5f;

        private static int _reports;
        private static float _lastReport = -99f;
        private static bool _refs;
        private static FieldInfo _localPlayer, _doodadField, _maxDistance, _hovering;
        private static MethodInfo _isAttached, _isValid, _getPosition;
        private static object _jump;

        private static void Refs()
        {
            if (_refs) return;
            _refs = true;
            Type player = TypeCache.Get("Player");
            Type character = TypeCache.Get("Character");
            if (player != null)
            {
                _localPlayer = AccessTools.Field(player, "m_localPlayer");
                _doodadField = AccessTools.Field(player, "m_doodadController");
                _maxDistance = AccessTools.Field(player, "m_maxInteractDistance");
                _hovering    = AccessTools.Field(player, "m_hovering");
            }
            if (character != null) _isAttached = AccessTools.Method(character, "IsAttached");
            Type doodad = TypeCache.Get("IDoodadController");
            if (doodad != null)
            {
                _isValid     = AccessTools.Method(doodad, "IsValid");
                _getPosition = AccessTools.Method(doodad, "GetPosition");
            }
            // Resolved HERE rather than borrowed from DirectActionInvoker, which only resolves its
            // actions if its own config entry is on. jumpHeld is the field that tells
            // UnmountIfJumping apart from every other release path (MountedAttackUtils.cs:17), so a
            // silent "False" from an unresolved action would be worse than no field at all.
            if (SteamVRProbe.Init())
            {
                Type actions = TypeCache.Get("Valve.VR.SteamVR_Actions");
                PropertyInfo p = actions == null
                    ? null : actions.GetProperty("valheim_Jump", BindingFlags.Static | BindingFlags.Public);
                _jump = p == null ? null : p.GetValue(null, null);
            }
        }

        // "True", "False" or "unreadable" - never a bare False for an action we could not find.
        private static string JumpHeld()
        {
            if (_jump == null) return "unreadable";
            object r = SteamVRProbe.Call(_jump, "GetState", SteamVRProbe.Any);
            if (r is bool && (bool)r) return "True";
            r = SteamVRProbe.Call(_jump, "GetState", SteamVRProbe.Left);
            if (r is bool && (bool)r) return "True";
            r = SteamVRProbe.Call(_jump, "GetState", SteamVRProbe.Right);
            if (r is bool && (bool)r) return "True";
            return "False";
        }

        internal static bool HasDoodad()
        {
            try
            {
                Refs();
                object p = _localPlayer == null ? null : _localPlayer.GetValue(null);
                return p != null && _doodadField != null && _doodadField.GetValue(p) != null;
            }
            catch { return false; }
        }

        // Everything that decided the release, read while the doodad is still in place. Returns null
        // when nothing is held, which is how the hooks below tell "this call released something"
        // from "this call was reached and did nothing".
        internal static string Snapshot()
        {
            // Capped first, because the VHVR hook's prefix is reached on every Use-with-nothing-
            // hovered and every mounted attack, not only on a release. Once the twelve reports are
            // spent this whole probe costs one integer compare.
            if (_reports >= MaxReports) return null;
            try
            {
                Refs();
                object p = _localPlayer == null ? null : _localPlayer.GetValue(null);
                if (p == null || _doodadField == null) return null;
                object doodad = _doodadField.GetValue(p);
                if (doodad == null) return null;

                // The distance check is the one release path neither VHVR transpiler touches
                // (av.il:68856-68868), so its two operands are the first thing to read.
                string range = "?";
                try
                {
                    Component body = p as Component;
                    if (body != null && _getPosition != null && _maxDistance != null)
                    {
                        Vector3 at = (Vector3)_getPosition.Invoke(doodad, null);
                        float d = Vector3.Distance(at, body.transform.position);
                        float max = Convert.ToSingle(_maxDistance.GetValue(p));
                        range = d.ToString("F2") + "/" + max.ToString("F2")
                              + (d > max ? " OVER" : "");
                    }
                }
                catch { }

                string valid = "?";
                try { if (_isValid != null) valid = Convert.ToString(_isValid.Invoke(doodad, null)); }
                catch { }
                string hovering = "?";
                try
                {
                    object h = _hovering == null ? null : _hovering.GetValue(p);
                    hovering = h == null || h.Equals(null) ? "none" : ((Component)h).name;
                }
                catch { }
                string attached = "?";
                try { if (_isAttached != null) attached = Convert.ToString(_isAttached.Invoke(p, null)); }
                catch { }

                return "doodad=" + doodad.GetType().Name
                     + " distance=" + range
                     + " doodadValid=" + valid
                     + " hovering=" + hovering
                     + " attached=" + attached
                     + " jumpHeld=" + JumpHeld()
                     + " takeInput=" + HelmWatch.TakeInputState();
            }
            catch { return null; }
        }

        internal static void Emit(string via, string snapshot)
        {
            if (snapshot == null || _reports >= MaxReports) return;
            float now = Time.realtimeSinceStartup;
            if (now - _lastReport < MinGapSeconds) return;
            _reports++;
            _lastReport = now;
            NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                + "helm RELEASED by " + via + " <- " + Caller() + "; " + snapshot
                + " - note StopDoodadControl does NOT detach (av.il:68709-68733), so attached=True"
                + " with no doodad is expected and is not evidence of which path fired");
        }

        // Frames 0 and 1 are this method and Emit; anything declared here is skipped as well,
        // because Harmony's generated wrapper sits between us and the real caller.
        private static string Caller()
        {
            try
            {
                System.Diagnostics.StackTrace trace = new System.Diagnostics.StackTrace(2, false);
                string chain = "";
                int shown = 0;
                for (int i = 0; i < trace.FrameCount && shown < 4; i++)
                {
                    MethodBase m = trace.GetFrame(i).GetMethod();
                    if (m == null) continue;
                    Type owner = m.DeclaringType;
                    if (owner == typeof(DoodadRelease)) continue;
                    chain += (chain.Length == 0 ? "" : " <- ") + (owner == null ? "?" : owner.Name) + "." + m.Name;
                    shown++;
                }
                return chain.Length == 0 ? "?" : chain;
            }
            catch { return "?"; }
        }
    }

    // The game's own release. Public and present in the IL at av.il:68709, so this is an ordinary
    // annotated patch rather than a guarded one.
    [HarmonyPatch(typeof(Player), "StopDoodadControl")]
    internal static class DoodadRelease_Vanilla
    {
        private static void Prefix(out string __state) { __state = DoodadRelease.Snapshot(); }
        private static void Postfix(string __state) { DoodadRelease.Emit("Player.StopDoodadControl", __state); }
    }

    // VHVR's substitute, which is the path a vanilla StopDoodadControl hook cannot see. Guarded,
    // because a client without VHVR must still load this plugin.
    [HarmonyPatch]
    internal static class DoodadRelease_Vhvr
    {
        private const string Target = "ValheimVRMod.Utilities.MountedAttackUtils:UnmountIfJumping";

        private static bool Prepare() { return AccessTools.Method(Target) != null; }
        private static MethodBase TargetMethod() { return AccessTools.Method(Target); }

        // Reached on every Use-with-nothing-hovered and every mounted attack, because the
        // transpilers put it exactly where the vanilla call was (ControlPatches.cs:910-958) - but it
        // only NULLS the doodad while valheim_Jump is held (MountedAttackUtils.cs:17). So the report
        // is conditional on the doodad actually having gone, which is what makes this quiet.
        private static void Prefix(out string __state) { __state = DoodadRelease.Snapshot(); }

        private static void Postfix(string __state)
        {
            if (__state == null || DoodadRelease.HasDoodad()) return;
            DoodadRelease.Emit("MountedAttackUtils.UnmountIfJumping", __state);
        }
    }

    // Why do boat controls stop answering after ten or twenty seconds?
    //
    // Nothing in the source explains it, and that is a finding rather than a shrug. There is no
    // timer in that range anywhere on the path: not in this plugin, not in VHVR's ship or control
    // code (its only countdowns are the 0.0625s and 0.5s autorun delays, VRControls.cs:25-26), and
    // not in the game - Ship::ApplyControlls throttles its Rudder RPC at 0.2s and
    // PlayerController::takeInputDelay is set to 0.2s, and those are the only clocks near the
    // steering path.
    //
    // What the path IS, quoted, because everything below is measured against it. The helm does not
    // use this plugin to steer at all: nothing feeds a ship from MountControls' seated stick
    // readers - Step() and Note() have no callers, and ApiLeftX/ApiLeftY are read only by the log
    // line below - while RawRightStickY serves the hover ring (HoverMenu.cs:314). Steering is VHVR
    // feeding the game's own path:
    //   ShipControlls::RPC_RequestRespons  -> Player::StartDoodadControl + Character::AttachStart
    //     (av.il:419560-419574, attach point plus onShip=true - so a helm DOES attach you)
    //   VHVR ControlPatches.cs:95-141      -> postfixes ZInput.GetJoyLeftStickX/Y and, while
    //     Player.IsAttached(), replaces the value with the VR stick behind a 0.3 deadzone on X and
    //     a 0.9 deadzone on Y
    //   PlayerController::FixedUpdate      -> reads GetJoyLeftStickX(false)/GetJoyLeftStickY(true)
    //     into moveDir (av.il:72578-72586) and calls Player::SetControls
    //   Player::SetControls                -> Player::SetDoodadControlls (av.il:68994)
    //   ShipControlls::ApplyControlls      -> Ship::ApplyControlls (av.il:419409-419411)
    //
    // Four mechanisms on that path drop the helm with no timer at all, which is what "it timed out"
    // would look like from the chair. As of 2026-08-25 they are resolved against a real session
    // rather than merely listed:
    //   1. Player::Update - a Use press with nothing hovered calls StopDoodadControl
    //      (av.il:68856 region, IL_017f-IL_01ca).
    //   2. Player::SetControls - jump, attack, secondaryAttack or dodge while holding a doodad
    //      calls StopDoodadControl (av.il:69004-69013; those are args 8, 2, 4 and 12 of the
    //      signature at av.il:68930).
    //      VHVR rewrites the StopDoodadControl call in BOTH of those methods to
    //      MountedAttackUtils.UnmountIfJumping (ControlPatches.cs:910-958), and the substitute is
    //      NOT equivalent: it nulls Player.m_doodadController itself (MountedAttackUtils.cs:26) and
    //      it fires on valheim_Jump being merely HELD - GetState, not GetStateDown
    //      (MountedAttackUtils.cs:17). A held jump therefore takes the helm with no press. This is
    //      the leading candidate for the 36.6s loss, and DoodadRelease above now names it if so.
    //   3. Player::UpdateDoodadControls - distance over m_maxInteractDistance calls
    //      StopDoodadControl (av.il:68856-68868), and so does !IsValid() (av.il:68823-68830).
    //      Neither transpiler touches this method. Player::UpdateAttach pins the body to the attach
    //      point every frame, so range is normally unreachable; what could still open it is VHVR
    //      translating the player transform for a real physical step while the helm's position
    //      follows the ship's moving Rigidbody. For a ShipControlls, IsValid() is only "this !=
    //      null" (its whole body is one op_Implicit), so that route needs the ship object itself to
    //      go away - a ZDO unloading under a fast boat would do it.
    //   4. VHVR ShipSteering.cs:119-131 - while EITHER grip is held at a helm, VHVR latches
    //      isSteering and then writes ship.ApplyControlls(hand velocity) every physics tick, which
    //      is zero turn for a hand slower than 0.05 m/s, in the same tick the thumbstick writes to
    //      the same rudder.
    //      EXONERATED for the 2026-08-25 session: gripL and gripR were False on all 41 samples, and
    //      with neither grip down ShipSteering.FixedUpdate reaches no ApplyControlls call at all -
    //      isSteering goes false at :56-59, isDoubleGrabbing at :73-76, and :100-131 then fall
    //      through to the end of the method.
    //
    // A fifth mechanism, and the one the session actually shows: PlayerController::FixedUpdate does
    // NOTHING with the stick unless PlayerController::TakeInput(false) returns true. When it is
    // false the method calls SetControls(Vector3.zero, and eleven falses) and returns
    // (av.il:72483-72503), so moveDir never reaches Ship::ApplyControlls and m_rudderValue cannot
    // move - which is exactly the measured "gameStick.x=0.64, rudder=0.00". Nothing else on the
    // path can zero moveDir: the takeInputDelay branch still passes it through (av.il:72876-72896),
    // and Ship::ApplyControlls turns the rudder for ANY non-zero dir.x (av.il:416333-416338).
    //
    // And a release does not detach you. Player::StopDoodadControl is OnUseStop, a ZLog line and
    // "m_doodadController = null" - no AttachStop (av.il:68709-68733). The only thing that drops
    // the attach afterwards is Player::SetControls, and only while moveDir is non-zero or a button
    // is down (av.il:68942-68982). So a helm lost while TakeInput is false leaves the player SEATED
    // at dead controls indefinitely, because the blocked FixedUpdate hands SetControls a zero
    // vector and the detach condition can never be met. That is the state he was left in.
    //
    // So this records the one line that tells them apart, at Message level so the operator's log
    // levels (Fatal, Error, Warning, Message) keep it. Not a guess and not per-frame chatter: it
    // logs when the record materially changes, and once the moment the helm is lost.
    internal static class HelmWatch
    {
        private static bool _dead;
        private static bool _refs;
        private static FieldInfo _localPlayer, _rudder, _speed;
        private static MethodInfo _isAttached, _takeInput;
        private static PropertyInfo _mainActive;
        private static Type _controllerType;
        private static object _controller;
        private static Type _shipType;
        private static float _heldSince = -1f, _nextSample, _lastLogged;
        private static bool _atHelm;
        private static string _shape;
        private static int _lines;

        // Sampled twice a second, written only on a change or every 30s, and capped for the session.
        // The whole value of this line is that an operator can read it after a sail; a per-frame
        // trace of a steady boat would bury the one transition that matters.
        private const float SampleSeconds = 0.5f;
        private const float HeartbeatSeconds = 30f;
        private const int MaxLines = 300;

        private static void Refs()
        {
            if (_refs) return;
            _refs = true;
            Type player = TypeCache.Get("Player");
            Type character = TypeCache.Get("Character");
            if (player != null) _localPlayer = AccessTools.Field(player, "m_localPlayer");
            if (character != null) _isAttached = AccessTools.Method(character, "IsAttached");
            Type vr = TypeCache.Get("ValheimVRMod.VRCore.UI.VRControls");
            if (vr != null) _mainActive = vr.GetProperty("mainControlsActive", BindingFlags.Static | BindingFlags.Public);
            // The gate that decides whether the stick reaches the ship at all - see the fifth
            // mechanism above. It is a PRIVATE INSTANCE method on PlayerController, a separate
            // component from Player, so it needs both the type and the live instance. Invoking it
            // reflectively runs it through this plugin's own PanelInput postfix, which is
            // deliberate: the number worth recording is the one FixedUpdate would have seen, not
            // the vanilla value underneath the rescue.
            _controllerType = TypeCache.Get("PlayerController");
            if (_controllerType != null)
            {
                _takeInput = AccessTools.Method(_controllerType, "TakeInput", new Type[] { typeof(bool) });
            }
        }

        // "True", "False(gate)" or "?" - the one field this log was missing. The 2026-08-25 sail
        // could not be closed because nothing recorded it: gameStick.x was 0.64 and the rudder was
        // 0.00, which is consistent with exactly one thing, and there was no way to confirm it
        // without another session. Shared with DoodadRelease so a release line carries it too.
        internal static string TakeInputState()
        {
            try
            {
                Refs();
                if (_takeInput == null || _localPlayer == null || _controllerType == null) return "?";
                if (_controller == null || (_controller is Component && ((Component)_controller) == null))
                {
                    Component body = _localPlayer.GetValue(null) as Component;
                    _controller = body == null ? null : body.GetComponent(_controllerType);
                }
                if (_controller == null) return "?";
                bool taken = Convert.ToBoolean(_takeInput.Invoke(_controller, new object[] { false }));
                if (taken) return "True";
                string gate = PanelInput.BlockingGate();
                // Whether the VR rescue stood down as well, because "a panel is shut" and "a panel
                // is shut and nothing will reopen it" are different diagnoses. At a helm the rescue
                // always declines (PanelInput.cs:176-180 - attachment routes movement into the
                // mounted controller), so this is expected to read "declined" and the gate name is
                // the actionable half.
                return "False(" + gate + "," + (PanelInput.WouldDecline(gate) ? "declined" : "allowed") + ")";
            }
            catch { return "?"; }
        }

        internal static void Tick()
        {
            if (_dead) return;
            if (NeuralyzeVRFixesPlugin.WatchHelm == null || !NeuralyzeVRFixesPlugin.WatchHelm.Value) return;
            try
            {
                object ship = DirectActions.AtHelm() ? DirectActions.SteeredShip() : null;
                float now = Time.realtimeSinceStartup;

                if (ship == null)
                {
                    if (_atHelm)
                    {
                        // The single most valuable line in this watch: the helm went away, and it
                        // says how long it lasted and what the last state was. DoodadRelease above
                        // names the path in its own line; if THIS line appears without one, the
                        // doodad was nulled by a writer neither hook covers, which is itself the
                        // finding.
                        //
                        // It used to claim "the GAME released the helm". That was wrong twice over:
                        // VHVR's MountedAttackUtils.UnmountIfJumping nulls the field directly
                        // (MountedAttackUtils.cs:26) so the release need not be the game's at all,
                        // and StopDoodadControl does not detach (av.il:68709-68733) so attached=True
                        // alongside a null doodad is the expected shape rather than a contradiction.
                        NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                            + "helm LOST after " + (now - _heldSince).ToString("F1")
                            + "s; last record was " + (_shape ?? "(none)")
                            + " takeInput=" + TakeInputState()
                            + " - m_doodadController is null, so the stick cannot reach a ship nobody"
                            + " is steering; look for the 'helm RELEASED by' line for the path");
                    }
                    _atHelm = false;
                    _heldSince = -1f;
                    _shape = null;
                    return;
                }

                if (!_atHelm)
                {
                    _atHelm = true;
                    _heldSince = now;
                    _nextSample = 0f;
                }
                if (now < _nextSample) return;
                _nextSample = now + SampleSeconds;
                if (_lines >= MaxLines) return;

                Refs();
                string key;
                string record = Record(ship, now, out key);
                // Compared on a COARSE key, never on the record itself. The record carries the held
                // time and two-decimal floats, so comparing it would differ every single sample and
                // this watch would write two lines a second into the operator's disk log. The key
                // buckets exactly the things a diagnosis turns on - is the stick arriving, is the
                // rudder moving, is a grip down, roughly where is the head - so a boat sailing
                // steadily is one line, and the moment any of those changes is the next one.
                if (key == _shape && now - _lastLogged < HeartbeatSeconds) return;
                _shape = key;
                _lastLogged = now;
                _lines++;
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag + "helm watch " + record);
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                    + "helm watch DISABLED for this session after: " + e.Message);
            }
        }

        private static string Record(object ship, float now, out string key)
        {
            if (_shipType == null || !_shipType.IsInstanceOfType(ship))
            {
                _shipType = ship.GetType();
                _rudder = AccessTools.Field(_shipType, "m_rudderValue");
                _speed = AccessTools.Field(_shipType, "m_speed");
            }

            // Three readings of the SAME axis now, not two. vhvr is VHVR's rotated stick, game is
            // what the game's own reader answers after every postfix including ours, and raw is the
            // untouched thumbstick. vhvr=(0,0) with the stick pushed means VHVR stopped reporting;
            // raw non-zero with game=(0,0) means a deadzone ate it; game non-zero with the rudder
            // not following means the input never reached the ship, which is takeInput below.
            float gameX = 0f, gameY = 0f;
            try { gameX = ZInput.GetJoyLeftStickX(false); gameY = ZInput.GetJoyLeftStickY(true); }
            catch { }
            float vrX = MountControls.ApiLeftX(), vrY = MountControls.ApiLeftY();
            string speed = Str(_speed, ship);
            string attached = Attached();
            string main = MainActive();
            bool gripL = DirectActionInvoker.LeftGrabHeld(), gripR = DirectActionInvoker.RightGrabHeld();
            float rudder = 0f;
            bool haveRudder = false;
            try
            {
                if (_rudder != null) { rudder = Convert.ToSingle(_rudder.GetValue(ship)); haveRudder = true; }
            }
            catch { }
            float yaw = HeadYaw(ship);
            string takeInput = TakeInputState();
            Vector2 raw = ShipStick.Raw();
            string hud = ShipHud();

            key = Bucket(vrX) + Bucket(vrY) + Bucket(gameX) + Bucket(gameY)
                + (haveRudder ? Bucket(rudder) : "?")
                + speed + attached + main
                + (gripL ? "L" : "-") + (gripR ? "R" : "-")
                // 45 degree steps: enough to show the head swinging off the boat's heading, which is
                // what would rotate VHVR's look-locomotion stick out of the axis the ship reads,
                // without turning every idle head wobble into a log line.
                + (float.IsNaN(yaw) ? "?" : Mathf.RoundToInt(yaw / 45f).ToString())
                // In the key on purpose, and this is the whole point of the revision: "could turn
                // then couldnt turn" is a TRANSITION, so the gate has to be able to start a new
                // line by itself or the log records the symptom and not the cause.
                + "|" + takeInput + "|" + hud;

            return "held=" + (now - _heldSince).ToString("F1") + "s"
                + " helm=" + _shipType.Name
                + " attached=" + attached
                + " takeInput=" + takeInput
                + " rawStick=(" + raw.x.ToString("F2") + "," + raw.y.ToString("F2") + ")"
                + " vhvrStick=(" + vrX.ToString("F2") + "," + vrY.ToString("F2") + ")"
                + " gameStick=(" + gameX.ToString("F2") + "," + gameY.ToString("F2") + ")"
                + " rudder=" + (haveRudder ? rudder.ToString("F2") : "?")
                + " speed=" + speed
                + " shipHud=" + hud
                + " gripL=" + gripL
                + " gripR=" + gripR
                + " headOffHeading=" + (float.IsNaN(yaw) ? "?" : yaw.ToString("F0")) + "deg"
                + " vhvrMainControls=" + main;
        }

        // "i cant see the direction wheel or boat speed indicators anymore in hud", recorded rather
        // than reasoned about. Hud::UpdateShipHud hides m_shipHudRoot whenever
        // Player::GetControlledShip() is null and then returns early if Hud::IsVisible() is false
        // (av.il:135532-135550), and Hud::IsVisible is itself only "m_rootObject localPosition.x <
        // 1000" (av.il:134766-134773) - the game parks the whole HUD off-screen rather than
        // deactivating it. So both halves are worth one field: an inactive root with a visible Hud
        // means the ship was not resolved, and a parked Hud means something moved the root.
        private static string ShipHud()
        {
            try
            {
                if (Hud.instance == null) return "noHud";
                GameObject root = Hud.instance.m_shipHudRoot;
                return (root == null ? "noRoot" : (root.activeInHierarchy ? "on" : "off"))
                     + "/" + (Hud.instance.IsVisible() ? "hudShown" : "hudParked");
            }
            catch { return "?"; }
        }

        // Sign with a deadband, because the question a diagnosis asks of every one of these numbers
        // is "is it there at all", not "what is it to two places".
        private static string Bucket(float v)
        {
            if (v > 0.15f) return "+";
            if (v < -0.15f) return "-";
            return "0";
        }

        private static string Attached()
        {
            try
            {
                if (_localPlayer == null || _isAttached == null) return "?";
                object p = _localPlayer.GetValue(null);
                return p == null ? "?" : Convert.ToString(Convert.ToBoolean(_isAttached.Invoke(p, null)));
            }
            catch { return "?"; }
        }

        private static string MainActive()
        {
            try { return _mainActive == null ? "?" : Convert.ToString(_mainActive.GetValue(null, null)); }
            catch { return "?"; }
        }

        // Where the head points relative to where the boat points.
        //
        // VHVR's left stick is not a raw axis while look locomotion is on: GetJoyLeftStickInput
        // rotates it into the player's frame using the configured joystick forward
        // (VRControls.cs:614-638). Player::UpdateAttach locks the character's rotation to the helm's
        // (av.il:68432-68447), so turning only your HEAD leaves the two frames apart, and at a
        // right angle the axis the ship reads has no stick left in it. That is a rotation, not a
        // timeout, and it would look exactly like one - so the offset is recorded.
        private static float HeadYaw(object ship)
        {
            try
            {
                Component shipComponent = ship as Component;
                Camera cam = Camera.main;
                if (shipComponent == null || cam == null) return float.NaN;
                Vector3 heading = shipComponent.transform.forward;
                Vector3 look = cam.transform.forward;
                heading.y = 0f;
                look.y = 0f;
                if (heading.sqrMagnitude < 0.0001f || look.sqrMagnitude < 0.0001f) return float.NaN;
                return Vector3.SignedAngle(heading.normalized, look.normalized, Vector3.up);
            }
            catch { return float.NaN; }
        }

        private static string Str(FieldInfo field, object target)
        {
            try { return field == null ? "?" : Convert.ToString(field.GetValue(target)); }
            catch { return "?"; }
        }
    }
}
