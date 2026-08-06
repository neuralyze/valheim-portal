using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Every probe here declares whether it could actually read its source. Three separate
    // instruments in this project silently returned zero and each zero was read as a
    // finding rather than a failure: the HUD delta probe, the swing velocity probe, and
    // XRSettings eye resolution. A probe that cannot tell "measured zero" from "failed to
    // measure" is worse than no probe, so each announces INSTRUMENT OK / INSTRUMENT DEAD
    // once, with the value that justifies the verdict.
    internal static class ProbeHealth
    {
        private static readonly HashSet<string> _announced = new HashSet<string>();

        internal static void Announce(string probe, bool alive, string detail)
        {
            if (!_announced.Add(probe)) return;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + (alive ? "INSTRUMENT OK   " : "INSTRUMENT DEAD ") + probe + " :: " + detail);
        }
    }

    // A high mean with a tight spread is steady-state cost, which mod removal or render
    // scale can address. A low mean with a fat p99 is stalls - GC, asset loads, spawn
    // churn - and needs an entirely different fix. Average FPS cannot distinguish them,
    // which is why "frames=26" was never enough to act on.
    internal sealed class FrameStats
    {
        private readonly List<float> _samples = new List<float>(4096);
        private float _windowStart;
        internal string Label = "frame";

        internal void Reset()
        {
            _samples.Clear();
            _windowStart = Time.realtimeSinceStartup;
        }

        internal void Sample(float deltaMs) { _samples.Add(deltaMs); }
        internal int Count { get { return _samples.Count; } }

        // Emitted next to the human-readable line so the onboarding ingest can parse
        // fixed keys rather than scraping prose that may be reworded later.
        internal string Machine(string kind)
        {
            if (_samples.Count == 0) return null;
            float[] s = _samples.ToArray();
            Array.Sort(s);
            double sum = 0d;
            for (int i = 0; i < s.Length; i++) sum += s[i];
            float mean = (float)(sum / s.Length);
            return "PERF " + kind
                + " fps=" + (mean > 0f ? (1000f / mean).ToString("F1") : "0")
                + " mean=" + mean.ToString("F2")
                + " p50=" + Pct(s, 0.50f).ToString("F2")
                + " p95=" + Pct(s, 0.95f).ToString("F2")
                + " p99=" + Pct(s, 0.99f).ToString("F2")
                + " min=" + s[0].ToString("F2")
                + " max=" + s[s.Length - 1].ToString("F2")
                + " n=" + s.Length;
        }

        internal string Report()
        {
            if (_samples.Count == 0) return Label + ": no samples";
            float[] s = _samples.ToArray();
            Array.Sort(s);
            double sum = 0d;
            for (int i = 0; i < s.Length; i++) sum += s[i];
            float mean = (float)(sum / s.Length);
            return Label
                + " n=" + s.Length
                + " fps=" + (mean > 0f ? (1000f / mean).ToString("F1") : "0")
                + " mean=" + mean.ToString("F2")
                + " p50=" + Pct(s, 0.50f).ToString("F2")
                + " p95=" + Pct(s, 0.95f).ToString("F2")
                + " p99=" + Pct(s, 0.99f).ToString("F2")
                + " min=" + s[0].ToString("F2")
                + " max=" + s[s.Length - 1].ToString("F2") + " ms";
        }

        private static float Pct(float[] sorted, float q)
        {
            int idx = Mathf.Clamp((int)Math.Round(q * (sorted.Length - 1)), 0, sorted.Length - 1);
            return sorted[idx];
        }
    }

    // Authoritative VR numbers read from OpenVR rather than Unity. UnityEngine.XR.XRSettings
    // reported eyeTexture 0x0 on this client because the SteamVR/OpenVR path never populates
    // it, so render resolution - the number deciding whether the client is pixel-bound -
    // was simply unknown for the whole session.
    internal static class VrSystemProbe
    {
        private static bool _resolved, _dead;
        private static PropertyInfo _sceneWidth, _sceneHeight;
        private static object _steamVrInstance, _compositor;
        private static Type _timingType;
        private static MethodInfo _getFrameTiming;

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            try
            {
                Type steamVr = AccessTools.TypeByName("Valve.VR.SteamVR");
                if (steamVr != null)
                {
                    PropertyInfo inst = steamVr.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                    _steamVrInstance = inst == null ? null : inst.GetValue(null, null);
                    if (_steamVrInstance != null)
                    {
                        _sceneWidth = steamVr.GetProperty("sceneWidth", BindingFlags.Instance | BindingFlags.Public);
                        _sceneHeight = steamVr.GetProperty("sceneHeight", BindingFlags.Instance | BindingFlags.Public);
                    }
                }
                Type openVr = AccessTools.TypeByName("Valve.VR.OpenVR");
                PropertyInfo comp = openVr == null ? null : openVr.GetProperty("Compositor", BindingFlags.Static | BindingFlags.Public);
                _compositor = comp == null ? null : comp.GetValue(null, null);
                _timingType = AccessTools.TypeByName("Valve.VR.Compositor_FrameTiming");
                if (_compositor != null && _timingType != null)
                {
                    foreach (MethodInfo m in _compositor.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public))
                    {
                        if (m.Name == "GetFrameTiming" && m.GetParameters().Length == 2) { _getFrameTiming = m; break; }
                    }
                }
            }
            catch (Exception e)
            {
                _dead = true;
                ProbeHealth.Announce("VrSystemProbe", false, "resolve threw " + e.GetType().Name + ": " + e.Message);
            }
        }

        internal static string Resolution()
        {
            Resolve();
            if (_dead) return "<probe dead>";
            try
            {
                if (_sceneWidth == null || _steamVrInstance == null)
                {
                    ProbeHealth.Announce("VrSystemProbe.resolution", false, "SteamVR.instance/sceneWidth unavailable");
                    return "<unavailable>";
                }
                float w = Convert.ToSingle(_sceneWidth.GetValue(_steamVrInstance, null));
                float h = Convert.ToSingle(_sceneHeight.GetValue(_steamVrInstance, null));
                bool alive = w > 0f && h > 0f;
                ProbeHealth.Announce("VrSystemProbe.resolution", alive,
                    alive ? "per-eye " + w + "x" + h : "read succeeded but returned " + w + "x" + h);
                // Stereo renders both eyes every frame; compare against 1920x1080 = 2.07 MPix.
                float mpix = (w * h * 2f) / 1000000f;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "PERF vr perEyeW=" + w.ToString("F0") + " perEyeH=" + h.ToString("F0")
                    + " mpixStereo=" + mpix.ToString("F2"));
                return w.ToString("F0") + "x" + h.ToString("F0") + " per eye = "
                     + mpix.ToString("F2") + " MPix/frame stereo (1080p desktop = 2.07)";
            }
            catch (Exception e) { return "<error " + e.Message + ">"; }
        }

        // The compositor is the only source that separates our GPU cost from the runtime's
        // and the only place reprojection and dropped frames are counted. High reprojection
        // explains judder without any HUD involvement, which is what the "flicker" reports
        // turned out to be.
        internal static string FrameTiming()
        {
            Resolve();
            if (_getFrameTiming == null || _timingType == null)
            {
                ProbeHealth.Announce("VrSystemProbe.timing", false, "OpenVR.Compositor.GetFrameTiming unavailable");
                return "<unavailable>";
            }
            try
            {
                object timing = Activator.CreateInstance(_timingType);
                FieldInfo sizeField = _timingType.GetField("m_nSize", BindingFlags.Instance | BindingFlags.Public);
                if (sizeField != null)
                {
                    int sz = 0;
                    try { sz = System.Runtime.InteropServices.Marshal.SizeOf(_timingType); } catch { }
                    if (sz > 0) sizeField.SetValue(timing, (uint)sz);
                }
                object[] args = { timing, (uint)0 };
                object ok = _getFrameTiming.Invoke(_compositor, args);
                timing = args[0];
                if (ok is bool && !(bool)ok)
                {
                    ProbeHealth.Announce("VrSystemProbe.timing", false, "GetFrameTiming returned false");
                    return "<no timing>";
                }
                string report = "present=" + F(timing, "m_nNumFramePresents")
                    + " dropped=" + F(timing, "m_nNumDroppedFrames")
                    + " reprojFlags=" + F(timing, "m_nReprojectionFlags")
                    + " gpuMs=" + F(timing, "m_flTotalRenderGpuMs")
                    + " compositorGpuMs=" + F(timing, "m_flCompositorRenderGpuMs")
                    + " clientIntervalMs=" + F(timing, "m_flClientFrameIntervalMs")
                    + " presentCpuMs=" + F(timing, "m_flPresentCallCpuMs")
                    + " waitGetPosesMs=" + F(timing, "m_flWaitGetPosesCalledMs");
                ProbeHealth.Announce("VrSystemProbe.timing", true, report);
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "PERF compositor present=" + F(timing, "m_nNumFramePresents")
                    + " dropped=" + F(timing, "m_nNumDroppedFrames")
                    + " reprojFlags=" + F(timing, "m_nReprojectionFlags")
                    + " gpuMs=" + F(timing, "m_flTotalRenderGpuMs")
                    + " compositorGpuMs=" + F(timing, "m_flCompositorRenderGpuMs")
                    + " clientIntervalMs=" + F(timing, "m_flClientFrameIntervalMs"));
                return report;
            }
            catch (Exception e)
            {
                ProbeHealth.Announce("VrSystemProbe.timing", false, "threw " + e.GetType().Name + ": " + e.Message);
                return "<error>";
            }
        }

        private static string F(object target, string field)
        {
            try
            {
                FieldInfo f = target.GetType().GetField(field, BindingFlags.Instance | BindingFlags.Public);
                if (f == null) return "n/a";
                object v = f.GetValue(target);
                return v is float ? ((float)v).ToString("F2") : Convert.ToString(v);
            }
            catch { return "err"; }
        }
    }

    // Every active camera is a render pass. VHVR adds a GUI camera and a world-space UI
    // camera on top of the game's own, and our canvas adoption can add more, so the count
    // is reported before any conclusion is drawn about GPU cost.
    internal static class CameraCensus
    {
        internal static string Report()
        {
            try
            {
                Camera[] all = Camera.allCameras;
                if (all == null || all.Length == 0)
                {
                    ProbeHealth.Announce("CameraCensus", false, "Camera.allCameras empty");
                    return "<none>";
                }
                var parts = new List<string>();
                foreach (Camera c in all)
                {
                    if (c == null) continue;
                    parts.Add(c.name + "(d=" + c.depth.ToString("F0")
                        + " " + (c.targetTexture != null ? c.targetTexture.width + "x" + c.targetTexture.height : "screen")
                        + " mask=0x" + c.cullingMask.ToString("X") + ")");
                }
                ProbeHealth.Announce("CameraCensus", true, all.Length + " active cameras");
                return all.Length + " active :: " + string.Join(" | ", parts.ToArray());
            }
            catch (Exception e) { return "<error " + e.Message + ">"; }
        }
    }

    // Per-assembly CPU attribution. Harmony-wraps the MonoBehaviour message loops declared
    // by types inside BepInEx plugin assemblies and accumulates wall time per assembly.
    //
    // Stated limits so the numbers are not over-read:
    //  - Only Update / LateUpdate / FixedUpdate are measured. A mod's Harmony patches on
    //    GAME methods, its coroutines, and GC pressure it causes are NOT attributed to it,
    //    so every figure is a LOWER BOUND on that mod's true cost.
    //  - Instrumenting hundreds of methods costs measurable time itself, so this is a
    //    measurement mode, off by default, not something to leave running.
    internal static class PluginProfiler
    {
        private sealed class Bucket { internal long Ticks; internal long Calls; }

        private static readonly Dictionary<string, Bucket> _buckets = new Dictionary<string, Bucket>();
        private static readonly Dictionary<MethodBase, string> _owner = new Dictionary<MethodBase, string>();
        private static bool _installed;
        private static int _patched, _refused;
        private static float _nextReport;
        private static long _windowStartTicks;
        private static int _windowFrames;

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            string[] names = { "Update", "LateUpdate", "FixedUpdate" };
            MethodInfo pre = typeof(PluginProfiler).GetMethod("Pre", BindingFlags.Static | BindingFlags.NonPublic);
            MethodInfo post = typeof(PluginProfiler).GetMethod("Post", BindingFlags.Static | BindingFlags.NonPublic);

            foreach (Assembly asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                string location;
                try { location = asm.Location ?? ""; } catch { continue; }
                if (location.IndexOf("plugins", StringComparison.OrdinalIgnoreCase) < 0) continue;
                if (asm == typeof(PluginProfiler).Assembly) continue;
                // Never instrument VHVR: it owns the camera, input and HUD paths this whole
                // plugin depends on, and wrapping its MonoBehaviour messages risks breaking
                // the very mechanics we are trying to measure.
                string an = asm.GetName().Name ?? "";
                if (an.IndexOf("ValheimVRMod", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                if (an.IndexOf("ValheimVR", StringComparison.OrdinalIgnoreCase) >= 0) continue;

                string label = asm.GetName().Name;
                Type[] types;
                try { types = asm.GetTypes(); }
                catch (ReflectionTypeLoadException e) { types = e.Types; }
                catch { continue; }

                foreach (Type t in types)
                {
                    if (t == null) continue;
                    // IsAssignableFrom forces the CLR to load this type's field types. A mod
                    // whose dependency assembly is absent throws TypeLoadException HERE, not
                    // in GetTypes() - AAA_Crafting referencing a missing ui_lib killed Awake
                    // and with it every feature of this plugin.
                    bool isBehaviour;
                    try { isBehaviour = typeof(MonoBehaviour).IsAssignableFrom(t); }
                    catch { _refused++; continue; }
                    if (!isBehaviour) continue;
                    foreach (string n in names)
                    {
                        MethodInfo m;
                        try
                        {
                            m = t.GetMethod(n, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
                                | BindingFlags.DeclaredOnly, null, Type.EmptyTypes, null);
                        }
                        catch { continue; }
                        if (m == null || m.IsAbstract || m.ContainsGenericParameters) continue;
                        try
                        {
                            harmony.Patch(m, prefix: new HarmonyMethod(pre), postfix: new HarmonyMethod(post));
                            _owner[m] = label;
                            _patched++;
                        }
                        catch { _refused++; }
                    }
                }
            }
            ProbeHealth.Announce("PluginProfiler", _patched > 0,
                "instrumented " + _patched + " MonoBehaviour message(s), " + _refused + " refused");
            _windowStartTicks = Stopwatch.GetTimestamp();
            _nextReport = Time.realtimeSinceStartup + 30f;
        }

        private static void Pre(out long __state) { __state = Stopwatch.GetTimestamp(); }

        private static void Post(long __state, MethodBase __originalMethod)
        {
            long elapsed = Stopwatch.GetTimestamp() - __state;
            string label;
            if (__originalMethod == null || !_owner.TryGetValue(__originalMethod, out label)) return;
            Bucket b;
            if (!_buckets.TryGetValue(label, out b)) { b = new Bucket(); _buckets[label] = b; }
            b.Ticks += elapsed;
            b.Calls++;
        }

        internal static void Tick()
        {
            if (!_installed) return;
            _windowFrames++;
            if (Time.realtimeSinceStartup < _nextReport) return;
            _nextReport = Time.realtimeSinceStartup + 30f;
            Report();
        }

        private static void Report()
        {
            long now = Stopwatch.GetTimestamp();
            double windowMs = (now - _windowStartTicks) * 1000.0 / Stopwatch.Frequency;
            int frames = Math.Max(1, _windowFrames);
            var rows = _buckets.Select(kv => new
            {
                Name = kv.Key,
                Ms = kv.Value.Ticks * 1000.0 / Stopwatch.Frequency,
                Calls = kv.Value.Calls
            }).OrderByDescending(r => r.Ms).ToList();
            double total = rows.Sum(r => r.Ms);

            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "=== plugin cost, " + windowMs.ToString("F0") + " ms / " + frames + " frames. "
                + "Update+LateUpdate+FixedUpdate ONLY - excludes each mod's Harmony patches on game "
                + "methods, its coroutines and its GC pressure, so these are LOWER BOUNDS ===");
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "  measured plugin total " + (total / frames).ToString("F3")
                + " ms/frame of " + (windowMs / frames).ToString("F2") + " ms/frame wall ("
                + (windowMs > 0 ? (100.0 * total / windowMs).ToString("F1") : "0") + "% attributed)");

            int shown = 0;
            foreach (var r in rows)
            {
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "  " + (r.Name.Length > 42 ? r.Name.Substring(0, 42) : r.Name).PadRight(42)
                    + (r.Ms / frames).ToString("F3") + " ms/frame  "
                    + (windowMs > 0 ? (100.0 * r.Ms / windowMs).ToString("F2") : "0") + "%  calls=" + r.Calls);
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "PERF plugin name=" + r.Name
                    + " msPerFrame=" + (r.Ms / frames).ToString("F4")
                    + " pct=" + (windowMs > 0 ? (100.0 * r.Ms / windowMs).ToString("F2") : "0")
                    + " calls=" + r.Calls);
                if (++shown >= 40) break;
            }
            _buckets.Clear();
            _windowFrames = 0;
            _windowStartTicks = now;
        }
    }

    // Decides GPU-bound versus CPU-bound by changing only the pixel count. Frame time that
    // falls with resolution is GPU work; frame time that does not move is CPU work, which no
    // amount of graphics tuning will help. Runs once, then restores the original scale.
    internal static class RenderScaleSweep
    {
        private static readonly float[] Steps = { 1.0f, 0.85f, 0.7f, 0.5f };
        private static readonly FrameStats _stats = new FrameStats();
        private static PropertyInfo _scale;
        private static float _original = -1f, _stepEnds;
        private static int _step = -1;
        private static bool _done, _dead;

        internal static bool Running { get { return _step >= 0 && !_done && !_dead; } }

        internal static void Tick(float deltaMs)
        {
            if (_done || _dead) return;
            try
            {
                if (_scale == null)
                {
                    Type xr = AccessTools.TypeByName("UnityEngine.XR.XRSettings");
                    _scale = xr == null ? null : xr.GetProperty("renderViewportScale", BindingFlags.Static | BindingFlags.Public);
                    if (_scale == null)
                    {
                        _dead = true;
                        ProbeHealth.Announce("RenderScaleSweep", false, "XRSettings.renderViewportScale unavailable");
                        return;
                    }
                    _original = Convert.ToSingle(_scale.GetValue(null, null));
                    // A scale of 0 means the property drives nothing on this pipeline, so a
                    // sweep would yield four identical numbers and a false "CPU-bound" call.
                    if (_original <= 0f)
                    {
                        _dead = true;
                        ProbeHealth.Announce("RenderScaleSweep", false,
                            "renderViewportScale reads " + _original + " - not driving this pipeline, sweep would be meaningless");
                        return;
                    }
                    ProbeHealth.Announce("RenderScaleSweep", true, "baseline scale " + _original);
                    Begin(0);
                    return;
                }
                _stats.Sample(deltaMs);
                if (Time.realtimeSinceStartup < _stepEnds) return;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "sweep " + _stats.Report());
                string sweepMachine = _stats.Machine("sweep scale=" + Steps[_step].ToString("F2"));
                if (sweepMachine != null) NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + sweepMachine);
                if (_step + 1 >= Steps.Length)
                {
                    _scale.SetValue(null, _original, null);
                    _done = true;
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "sweep done, scale restored to " + _original
                        + ". Frame time tracking pixels => GPU-bound; flat => CPU-bound.");
                    return;
                }
                Begin(_step + 1);
            }
            catch (Exception e)
            {
                _dead = true;
                try { if (_scale != null && _original > 0f) _scale.SetValue(null, _original, null); } catch { }
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "sweep failed: " + e.Message);
            }
        }

        private static void Begin(int step)
        {
            _step = step;
            _scale.SetValue(null, Steps[step], null);
            _stats.Reset();
            _stats.Label = "scale=" + Steps[step].ToString("F2");
            _stepEnds = Time.realtimeSinceStartup + 20f;
        }
    }

    internal static class ProfilerHub
    {
        private static readonly FrameStats _frame = new FrameStats();
        private static float _nextReport;
        private static bool _started;

        internal static void Tick()
        {
            float deltaMs = Time.unscaledDeltaTime * 1000f;
            if (!_started)
            {
                _started = true;
                _frame.Reset();
                _nextReport = Time.realtimeSinceStartup + 15f;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "VR resolution: " + VrSystemProbe.Resolution());
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "cameras: " + CameraCensus.Report());
            }

            if (NeuralyzeVRFixesPlugin.SweepRenderScale.Value)
            {
                RenderScaleSweep.Tick(deltaMs);
                if (RenderScaleSweep.Running) return;   // keep sweep frames out of the baseline
            }

            _frame.Sample(deltaMs);
            PluginProfiler.Tick();

            if (Time.realtimeSinceStartup < _nextReport) return;
            _nextReport = Time.realtimeSinceStartup + 15f;
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + _frame.Report());
            string machine = _frame.Machine("frame");
            if (machine != null) NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + machine);
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "compositor " + VrSystemProbe.FrameTiming());
            _frame.Reset();
        }
    }
}
