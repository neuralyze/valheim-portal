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
    // would look like from the chair:
    //   1. Player::Update - a Use press with nothing hovered calls StopDoodadControl
    //      (av.il:68856 region, IL_017f-IL_01ca).
    //   2. Player::SetControls - jump, attack, secondaryAttack or dodge while holding a doodad
    //      calls StopDoodadControl (av.il:69006-69016).
    //      Both of those are rewritten by VHVR to MountedAttackUtils.UnmountIfJumping, which
    //      unmounts only while valheim_Jump is held (ControlPatches.cs:906-955) - so they are
    //      covered ONLY for as long as those transpilers apply.
    //   3. Player::UpdateDoodadControls - distance from the doodad over m_maxInteractDistance calls
    //      StopDoodadControl (av.il:68856-68868). Neither transpiler touches this method.
    //   4. VHVR ShipSteering.cs:118-127 - while EITHER grip is held at a helm, VHVR latches
    //      isSteering and then writes ship.ApplyControlls(hand velocity) every physics tick, which
    //      is zero turn for a hand slower than 0.05 m/s, in the same tick the thumbstick writes to
    //      the same rudder.
    //
    // So this records the one line that tells them apart, at Message level so the operator's log
    // levels (Fatal, Error, Warning, Message) keep it. Not a guess and not per-frame chatter: it
    // logs when the record materially changes, and once the moment the helm is lost.
    internal static class HelmWatch
    {
        private static bool _dead;
        private static bool _refs;
        private static FieldInfo _localPlayer, _rudder, _speed;
        private static MethodInfo _isAttached;
        private static PropertyInfo _mainActive;
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
                        // says how long it lasted and what the last state was. If this is what the
                        // operator is hitting, the cause is one of mechanisms 1-3 above, and the
                        // next question is which - not whether.
                        NeuralyzeVRFixesPlugin.Log.LogMessage(NeuralyzeVRFixesPlugin.Tag
                            + "helm LOST after " + (now - _heldSince).ToString("F1")
                            + "s; last record was " + (_shape ?? "(none)")
                            + " - m_doodadController is null, so the GAME released the helm; the stick"
                            + " cannot reach a ship nobody is steering");
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

            // Two readings of the SAME axis, one on each side of VHVR's patch. vhvr=(0,0) with the
            // stick pushed means VHVR stopped reporting; vhvr non-zero with game=(0,0) means the
            // patch stopped being applied; both non-zero with the rudder not following means
            // something downstream is overwriting it - which is mechanism 4.
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

            key = Bucket(vrX) + Bucket(vrY) + Bucket(gameX) + Bucket(gameY)
                + (haveRudder ? Bucket(rudder) : "?")
                + speed + attached + main
                + (gripL ? "L" : "-") + (gripR ? "R" : "-")
                // 45 degree steps: enough to show the head swinging off the boat's heading, which is
                // what would rotate VHVR's look-locomotion stick out of the axis the ship reads,
                // without turning every idle head wobble into a log line.
                + (float.IsNaN(yaw) ? "?" : Mathf.RoundToInt(yaw / 45f).ToString());

            return "held=" + (now - _heldSince).ToString("F1") + "s"
                + " helm=" + _shipType.Name
                + " attached=" + attached
                + " vhvrStick=(" + vrX.ToString("F2") + "," + vrY.ToString("F2") + ")"
                + " gameStick=(" + gameX.ToString("F2") + "," + gameY.ToString("F2") + ")"
                + " rudder=" + (haveRudder ? rudder.ToString("F2") : "?")
                + " speed=" + speed
                + " gripL=" + gripL
                + " gripR=" + gripR
                + " headOffHeading=" + (float.IsNaN(yaw) ? "?" : yaw.ToString("F0")) + "deg"
                + " vhvrMainControls=" + main;
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
