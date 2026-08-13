using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Ascending and descending in debug fly mode.
    //
    // Character.UpdateMotion, decompiled from this build of the game, drives flight from
    // two different input APIs:
    //
    //     ascend   ZInput.GetButton("Jump")  ||  ZInput.GetButton("JoyJump")
    //     descend  ZInput.GetButtonPressedTimer("JoyCrouch")
    //
    // VHVR injects VR input by patching exactly three methods - GetButton, GetButtonDown
    // and GetButtonUp - so descend is unreachable in VR by construction: nothing feeds
    // GetButtonPressedTimer, and it returns zero however long the stick is held.
    //
    // Ascend goes through a patched method but still fails in practice, because VHVR's
    // GetButton path answers "Jump" from canJump() and this plugin invokes jump directly
    // rather than through that path. Rather than guess which link breaks, both reads are
    // answered here from the SteamVR actions, and only while the character is actually
    // debug-flying, so ordinary jumping and crouching are untouched.
    internal static class FlyControls
    {
        private static bool _resolved;
        private static MethodInfo _isDebugFlying;
        private static FieldInfo _debugFlyField;
        private static bool _announced;

        // Hold duration for the descend timer. The game only compares it against zero to
        // decide "is descent held", but it is accumulated honestly so a caller that reads
        // the magnitude gets something truthful.
        private static float _descendHeldSince = -1f;

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            _isDebugFlying = AccessTools.Method(typeof(Character), "IsDebugFlying");
            // Belt and braces: the method was read straight out of the game's IL, but a
            // silent null here is exactly how the first attempt failed - the gate simply
            // never opened and nothing said so. The backing field is the fallback.
            _debugFlyField = AccessTools.Field(typeof(Character), "m_debugFly")
                          ?? AccessTools.Field(typeof(Player), "m_debugFly");
        }

        private static int _flyFrame = -1;
        private static bool _flyValue;

        // Cached per frame. This is reached from a postfix on TryGetButtonState, which the
        // game calls for every button it reads - dozens of times a frame - and a reflective
        // Invoke on each of those is what made the frame rate collapse.
        internal static bool Flying()
        {
            long _t = HookProfiler.Start();
            try { return FlyingCached(); } finally { HookProfiler.Stop(HookProfiler.Fly, _t); }
        }

        private static bool FlyingCached()
        {
            if (_flyFrame == Time.frameCount) return _flyValue;
            _flyFrame = Time.frameCount;
            _flyValue = FlyingUncached();
            return _flyValue;
        }

        private static bool FlyingUncached()
        {
            Resolve();
            if (_isDebugFlying == null) return false;
            Player p = Player.m_localPlayer;
            if (p == null) return false;
            bool flying = false;
            try
            {
                if (_isDebugFlying != null) flying = (bool)_isDebugFlying.Invoke(p, null);
                if (!flying && _debugFlyField != null) flying = Convert.ToBoolean(_debugFlyField.GetValue(p));
            }
            catch { return false; }
            if (flying && !_announced)
            {
                _announced = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "fly detected: method=" + (_isDebugFlying != null)
                    + " field=" + (_debugFlyField != null));
            }
            return flying;
        }

        // Counts postfix hits so "the patch never ran" can be told apart from "the patch
        // ran and the game ignored it" - the two failures are indistinguishable in the
        // headset and I have already guessed wrong once by not measuring them.
        internal static int JumpReads;

        // The remaining gates on ascent, straight from Character.UpdateMotion:
        //     TakeInput() && (GetButton("Jump") || GetButton("JoyJump"))
        //         && takeInputDelay <= 0 && !Hud.IsPieceSelectionVisible()
        internal static string Gates()
        {
            Player p = Player.m_localPlayer;
            if (p == null) return "noPlayer";
            string takeInput = "?", delay = "?";
            try
            {
                MethodInfo m = AccessTools.Method(typeof(Character), "TakeInput");
                if (m != null) takeInput = m.Invoke(p, null).ToString();
                FieldInfo f = AccessTools.Field(typeof(Character), "takeInputDelay");
                if (f != null) delay = Convert.ToString(f.GetValue(null));
            }
            catch { }
            return "takeInput=" + takeInput + " inputDelay=" + delay
                + " jumpHeld=" + DirectActionInvoker.JumpHeld()
                + " jumpReads=" + JumpReads
                + " pieceMenu=" + BuildMenuProbe.PieceSelectionVisible();
        }

        internal static bool Enabled()
        {
            return NeuralyzeVRFixesPlugin.FlyControls != null
                && NeuralyzeVRFixesPlugin.FlyControls.Value;
        }

        // Descent is held while the crouch action is held. Crouch is the same action the
        // game uses for sneaking on the ground, which is why this is scoped to flight.
        internal static float DescendTimer(bool held)
        {
            if (!held)
            {
                _descendHeldSince = -1f;
                return 0f;
            }
            if (_descendHeldSince < 0f) _descendHeldSince = Time.realtimeSinceStartup;
            return Mathf.Max(0.01f, Time.realtimeSinceStartup - _descendHeldSince);
        }
    }

    // ZInput.GetButton(string) is four instructions around a delegate call, and a postfix
    // on it never executed: jumpReads stayed at 0 across a whole flight while every other
    // gate measured open. Mono inlines a method that small into callers that were already
    // compiled, so Harmony has nothing left to intercept - which is also why VHVR's own
    // injection into GetButtonDown cannot deliver the build menu button.
    //
    // TryGetButtonState is the instance method underneath it, big enough to survive as a
    // real call, and every button read funnels through it. Scoped to flight so that
    // forcing "Jump" true cannot cause repeated jumps on the ground.
    [HarmonyPatch(typeof(ZInput), "TryGetButtonState")]
    internal static class ZInput_TryGetButtonState_Fly
    {
        private static void Postfix(string name, ref bool __result)
        {
            if (__result || !FlyControls.Enabled()) return;
            if (name != "Jump" && name != "JoyJump") return;
            if (!FlyControls.Flying()) return;
            FlyControls.JumpReads++;
            __result = DirectActionInvoker.JumpHeld();
        }
    }

    [HarmonyPatch(typeof(ZInput), nameof(ZInput.GetButton))]
    internal static class ZInput_GetButton_Fly
    {
        private static void Postfix(string name, ref bool __result)
        {
            if (__result || !FlyControls.Enabled()) return;
            if (name != "Jump" && name != "JoyJump") return;
            // Deliberately NOT gated on debug-fly. The gate is what failed on the first
            // attempt - twelve jump invocations, no ascent, and no way to see why - and it
            // buys nothing here: the level of the jump button is a truthful answer to
            // "is Jump held" whoever asks. Vanilla jumping reads GetButtonDown, not
            // GetButton, so a held level does not cause repeated jumps on the ground.
            __result = DirectActionInvoker.JumpHeld();
        }
    }

    [HarmonyPatch(typeof(ZInput), nameof(ZInput.GetButtonPressedTimer))]
    internal static class ZInput_GetButtonPressedTimer_Fly
    {
        private static void Postfix(string name, ref float __result)
        {
            if (!FlyControls.Enabled()) return;
            if (name != "JoyCrouch" && name != "Crouch") return;
            if (!FlyControls.Flying()) { FlyControls.DescendTimer(false); return; }
            float held = FlyControls.DescendTimer(DirectActionInvoker.CrouchHeld());
            if (held > __result) __result = held;
        }
    }
}
