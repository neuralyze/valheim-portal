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
                Type vr = AccessTools.TypeByName("ValheimVRMod.VRCore.UI.VRControls");
                if (vr != null)
                {
                    _vrControls = vr.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    _stickY = AccessTools.Method(vr, "GetJoyRightStickY");
                    _stickX = AccessTools.Method(vr, "GetJoyRightStickX");
                    _leftStickX = AccessTools.Method(vr, "GetJoyLeftStickX");
                    _leftStickY = AccessTools.Method(vr, "GetJoyLeftStickY");
                }
                Type character = AccessTools.TypeByName("Character");
                Type player = AccessTools.TypeByName("Player");
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
                Type player = AccessTools.TypeByName("Player");
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

    // Answers the two buttons the game reads for vehicle speed, and only while seated.
    //
    // Scoped exactly as narrowly as the fly patch: outside a seat these names are untouched, so
    // walking, running and everything else keep reading the real input.
}
