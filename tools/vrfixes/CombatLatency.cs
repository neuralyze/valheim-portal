using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Measures swing-to-damage latency instead of reasoning about it.
    //
    // The player reports combat "feels a little lagged". There are at least six candidate
    // sources and static analysis can rank them but cannot price them:
    //   - render frame time: 36-50 ms per frame at the measured 20-28 FPS
    //   - the physics step, which is what actually drives OnTriggerEnter/OnTriggerStay
    //   - PhysicsEstimator's velocity window, if it is in snapshot mode rather than reading
    //     SteamVR tracked velocity, since a moving average lags the true peak
    //   - AttackTargetMeshCooldown swallowing a swing whose predecessor has not expired
    //   - other mods' patches on Attack.OnAttackTrigger and the damage path
    //   - compositor reprojection, which is felt lag no code change removes
    //
    // Method: mark the moment hand speed crosses the attack threshold, then mark when the
    // damage path actually runs, and report the delta in milliseconds AND in frames. Frames
    // matter because a delta of one frame at 25 FPS is 40 ms of pure frame-rate cost that no
    // amount of combat-code tuning will recover.
    internal static class CombatLatency
    {
        private sealed class Sample
        {
            internal double SwingMs;      // threshold crossed
            internal int SwingFrame;
            internal float Speed;
        }

        private static readonly Stopwatch _clock = Stopwatch.StartNew();
        private static Sample _pending;
        private static bool _installed, _dead;
        private static int _reported;

        // Distributions rather than single values: one sample proves nothing, and the
        // interesting question is whether the lag is constant (frame rate) or occasional
        // (a cooldown swallowing swings).
        private static readonly List<double> _toAttackStart = new List<double>();
        private static readonly List<double> _toDamage = new List<double>();
        private static readonly List<int> _framesToDamage = new List<int>();
        private static int _swings, _attacks, _damages, _swallowed;
        private static float _nextReport;

        private static PropertyInfo _rightEst, _leftEst;
        private static int _physicsSteps, _renderFrames;
        private static float _hmdHz = -1f;
        private static MethodInfo _swingReq;
        private static float _threshold = -1f;

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            try
            {
                // VHVR's prefix on Attack.Start IS the melee damage path, so timing its entry
                // measures when the game commits to the hit.
                Type attack = AccessTools.TypeByName("Attack");
                MethodInfo start = attack == null ? null : AccessTools.Method(attack, "Start");
                MethodInfo trigger = attack == null ? null : AccessTools.Method(attack, "OnAttackTrigger");
                MethodInfo onStart = typeof(CombatLatency).GetMethod("OnAttackStart", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo onTrigger = typeof(CombatLatency).GetMethod("OnDamagePath", BindingFlags.Static | BindingFlags.NonPublic);

                int n = 0;
                // Postfix, so we run after VHVR's prefix has done doMeleeAttack.
                if (start != null) { harmony.Patch(start, postfix: new HarmonyMethod(onStart)); n++; }

                // Attack.OnAttackTrigger alone is NOT the damage path. VHVR's Attack.Start prefix
                // returns false and applies damage itself, so the animation event fires only for
                // some attack kinds - a session spent chopping trees reported damageEvents=0 while
                // the axe was plainly working. Count damage where it is actually RECEIVED instead,
                // across every destructible the game uses, so the probe measures felled trees and
                // mined rock as well as creatures.
                int recv = 0;
                foreach (string typeName in new[] { "Character", "TreeBase", "TreeLog", "Destructible",
                                                    "WearNTear", "MineRock", "MineRock5" })
                {
                    Type t = AccessTools.TypeByName(typeName);
                    MethodInfo dmg = t == null ? null : AccessTools.Method(t, "Damage");
                    if (dmg == null) continue;
                    try
                    {
                        harmony.Patch(dmg, postfix: new HarmonyMethod(onTrigger));
                        recv++;
                    }
                    catch (Exception pe)
                    {
                        NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                            + "combat probe could not hook " + typeName + ".Damage: " + pe.Message);
                    }
                }
                if (trigger != null) { harmony.Patch(trigger, postfix: new HarmonyMethod(onTrigger)); recv++; }
                n += recv > 0 ? 1 : 0;

                Type vrp = AccessTools.TypeByName("ValheimVRMod.VRCore.VRPlayer");
                if (vrp != null)
                {
                    _rightEst = vrp.GetProperty("rightHandPhysicsEstimator", BindingFlags.Static | BindingFlags.Public);
                    _leftEst = vrp.GetProperty("leftHandPhysicsEstimator", BindingFlags.Static | BindingFlags.Public);
                }
                Type cfg = AccessTools.TypeByName("ValheimVRMod.Utilities.VHVRConfig");
                _swingReq = cfg == null ? null : cfg.GetMethod("SwingSpeedRequirement", BindingFlags.Static | BindingFlags.Public);

                ProbeHealth.Announce("CombatLatency", n > 0 && _rightEst != null,
                    "attackStart=" + (start != null) + " damageReceivers=" + recv
                    + " handEstimator=" + (_rightEst != null));
                _nextReport = Time.realtimeSinceStartup + 30f;
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "combat latency install failed: " + e.Message);
            }
        }

        // Watches for the instant hand speed crosses the threshold VHVR uses to accept a
        // swing. That is the earliest moment the game COULD have known a hit was intended,
        // so it is the correct zero point for latency.
        // SteamVR_Render.Update overwrites Time.fixedDeltaTime with 1/hmd_DisplayFrequency every
        // frame (lockPhysicsUpdateRateToRenderFrequency is 1 in its shipped settings asset), so
        // physics runs at 72-90 Hz rather than Valheim's own 50 Hz. At 20-28 FPS that is 3-5
        // complete FixedUpdate cycles per rendered frame, and none of the extra ones can see a new
        // hand pose because the weapon collider is only repositioned during rendering. Counting
        // them proves the waste instead of asserting it.
        internal static void PhysicsStep() { _physicsSteps++; }

        internal static void Tick()
        {
            if (_dead || !_installed) return;
            try
            {
                if (_threshold < 0f && _swingReq != null)
                    _threshold = Convert.ToSingle(_swingReq.Invoke(null, null));
                if (_threshold < 0f) return;

                float speed = Math.Max(Speed(_rightEst), Speed(_leftEst));
                // Fists gate at 45% of the configured requirement (FistCollision.hasMomentum),
                // so use the lower bound to catch the earliest plausible intent.
                float gate = _threshold * 0.45f;

                if (_pending == null)
                {
                    if (speed > gate)
                    {
                        _pending = new Sample
                        {
                            SwingMs = _clock.Elapsed.TotalMilliseconds,
                            SwingFrame = Time.frameCount,
                            Speed = speed
                        };
                        _swings++;
                    }
                }
                else if (_clock.Elapsed.TotalMilliseconds - _pending.SwingMs > 1200.0)
                {
                    // A swing that never produced an attack within 1.2 s was swallowed -
                    // by a cooldown, a missed collider, or a gate. Counted separately,
                    // because "occasionally nothing happens" is a different complaint from
                    // "everything is late" and the fixes differ.
                    _swallowed++;
                    _pending = null;
                }

                _renderFrames++;
                if (Time.realtimeSinceStartup >= _nextReport) Report();
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "combat latency tick failed: " + e.Message);
            }
        }

        private static void OnAttackStart()
        {
            try
            {
                _attacks++;
                if (_pending == null) return;
                _toAttackStart.Add(_clock.Elapsed.TotalMilliseconds - _pending.SwingMs);
            }
            catch { }
        }

        private static void OnDamagePath()
        {
            try
            {
                _damages++;
                if (_pending == null) return;
                _toDamage.Add(_clock.Elapsed.TotalMilliseconds - _pending.SwingMs);
                _framesToDamage.Add(Time.frameCount - _pending.SwingFrame);
                if (_reported < 12)
                {
                    _reported++;
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "swing->damage " + (_clock.Elapsed.TotalMilliseconds - _pending.SwingMs).ToString("F1")
                        + " ms / " + (Time.frameCount - _pending.SwingFrame) + " frames"
                        + " at " + _pending.Speed.ToString("F2") + " m/s");
                }
                _pending = null;
            }
            catch { }
        }

        private static float HmdHz()
        {
            if (_hmdHz > 0f) return _hmdHz;
            try
            {
                Type steamVr = AccessTools.TypeByName("Valve.VR.SteamVR");
                PropertyInfo inst = steamVr == null ? null : steamVr.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                object sv = inst == null ? null : inst.GetValue(null, null);
                FieldInfo f = sv == null ? null : AccessTools.Field(sv.GetType(), "hmd_DisplayFrequency");
                if (f != null) _hmdHz = Convert.ToSingle(f.GetValue(sv));
                else
                {
                    PropertyInfo pr = sv == null ? null : sv.GetType().GetProperty("hmd_DisplayFrequency");
                    if (pr != null) _hmdHz = Convert.ToSingle(pr.GetValue(sv, null));
                }
            }
            catch { }
            return _hmdHz > 0f ? _hmdHz : 0f;
        }

        private static float Speed(PropertyInfo prop)
        {
            try
            {
                object est = prop == null ? null : prop.GetValue(null, null);
                if (est == null) return 0f;
                object v = SteamVRProbe.Call(est, "GetVelocity", new object[] { null });
                if (!(v is Vector3)) v = SteamVRProbe.Call(est, "GetVelocity");
                return v is Vector3 ? ((Vector3)v).magnitude : 0f;
            }
            catch { return 0f; }
        }

        private static void Report()
        {
            _nextReport = Time.realtimeSinceStartup + 30f;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "PERF combat swings=" + _swings + " attackStarts=" + _attacks
                + " damageEvents=" + _damages + " swallowed=" + _swallowed
                + " threshold=" + _threshold.ToString("F2") + " fistGate=" + (_threshold * 0.45f).ToString("F2")
                + " fixedDelta=" + (Time.fixedDeltaTime * 1000f).ToString("F1") + "ms"
                + " physicsHz=" + (Time.fixedDeltaTime > 0f ? (1f / Time.fixedDeltaTime).ToString("F0") : "?")
                + " hmdHz=" + HmdHz().ToString("F0")
                // Needs a real sample window; the first report after load divided by almost no
                // frames and printed stepsPerFrame=5889, which is noise pretending to be data.
                + " stepsPerFrame=" + (_renderFrames >= 20 ? ((float)_physicsSteps / _renderFrames).ToString("F2") : "n/a")
                + " frameMs=" + (Time.unscaledDeltaTime * 1000f).ToString("F1"));
            _physicsSteps = 0; _renderFrames = 0;
            Emit("swing->attackStart", _toAttackStart, null);
            Emit("swing->damage", _toDamage, _framesToDamage);
            _toAttackStart.Clear(); _toDamage.Clear(); _framesToDamage.Clear();
        }

        private static void Emit(string label, List<double> ms, List<int> frames)
        {
            if (ms.Count == 0)
            {
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "PERF combat " + label + " no samples");
                return;
            }
            double[] a = ms.ToArray();
            Array.Sort(a);
            double sum = 0d;
            for (int i = 0; i < a.Length; i++) sum += a[i];
            string extra = "";
            if (frames != null && frames.Count > 0)
            {
                int[] f = frames.ToArray();
                Array.Sort(f);
                double fsum = 0d;
                for (int i = 0; i < f.Length; i++) fsum += f[i];
                extra = " framesMean=" + (fsum / f.Length).ToString("F2")
                      + " framesMax=" + f[f.Length - 1];
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "PERF combat " + label
                + " n=" + a.Length
                + " mean=" + (sum / a.Length).ToString("F1")
                + " p50=" + a[a.Length / 2].ToString("F1")
                + " min=" + a[0].ToString("F1")
                + " max=" + a[a.Length - 1].ToString("F1") + " ms" + extra);
        }
    }

    // Undoes SteamVR's per-frame override of the physics rate. SteamVR_Render.Update sets
    // Time.fixedDeltaTime = 1/hmd_DisplayFrequency on EVERY frame, so a one-shot assignment is
    // useless - the restore has to run after it, every frame.
    internal static class PhysicsRateRestorer
    {
        private const float VanillaStep = 0.02f;   // Valheim's own TimeManager value, 50 Hz
        private static bool _logged;

        internal static void Install(Harmony harmony)
        {
            try
            {
                Type render = AccessTools.TypeByName("Valve.VR.SteamVR_Render");
                MethodInfo update = render == null ? null : AccessTools.Method(render, "Update");
                if (update == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "SteamVR_Render.Update not found; physics rate left as SteamVR set it");
                    return;
                }
                harmony.Patch(update, postfix: new HarmonyMethod(
                    typeof(PhysicsRateRestorer).GetMethod("After", BindingFlags.Static | BindingFlags.NonPublic)));
                ProbeHealth.Announce("PhysicsRateRestorer", true, "patched SteamVR_Render.Update");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "physics rate restore failed: " + e.Message);
            }
        }

        private static void After()
        {
            if (Time.fixedDeltaTime == VanillaStep) return;
            if (!_logged)
            {
                _logged = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "physics rate restored: SteamVR set " + (Time.fixedDeltaTime * 1000f).ToString("F1")
                    + " ms (" + (1f / Time.fixedDeltaTime).ToString("F0") + " Hz), forcing 20.0 ms (50 Hz)");
            }
            Time.fixedDeltaTime = VanillaStep;
        }
    }
}
