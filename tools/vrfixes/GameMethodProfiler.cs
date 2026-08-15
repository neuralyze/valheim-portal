using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Where the frame actually goes, and who is standing in it.
    //
    // Measured on this install: 12 fps in play, the compositor holding 28-34ms of GPU per frame, and
    // roughly 40ms per frame of processor time that nothing accounted for. Every mod's Update,
    // LateUpdate and FixedUpdate together came to 3ms, so the cost is not in mod update loops - it is
    // in the game's own methods, which 111 mods have patched. The per-mod profiler cannot see that by
    // construction and says so in its own description.
    //
    // This is the inventory instrument aimed at the rest of the game: time the game's hot methods,
    // and for each one name every mod attached to it. A row with no third-party patches is Valheim's
    // own cost and no amount of mod removal will move it; a row with names is a shortlist. Nothing is
    // divided between owners, because attributing 40ms three ways would invent three numbers.
    //
    // ZoneSystem and ZNetScene are in the list deliberately: the session that motivated this reported
    // 2084 world objects resolvable but not instantiated, and second-long stalls while moving. If that
    // backlog is the cause, it shows up here as time inside CreateObjects.
    internal static class GameMethodProfiler
    {
        private sealed class Bucket
        {
            internal long Ticks;
            internal long Calls;
            internal MethodBase Method;
        }

        // Per-frame singletons and the world-streaming path. Deliberately NOT per-object methods
        // called hundreds of times a frame - Character.CustomFixedUpdate and BaseAI.UpdateAI run once
        // per creature, and wrapping them would cost more than they report. They are named here so
        // the omission is a decision rather than an oversight.
        private static readonly string[][] Targets =
        {
            new[] { "Player", "Update" },
            new[] { "Player", "FixedUpdate" },
            new[] { "Player", "PlayerAttackInput" },
            new[] { "Player", "UpdateEnvStatusEffects" },
            new[] { "ZNetScene", "Update" },
            new[] { "ZNetScene", "CreateObjects" },
            new[] { "ZNetScene", "CreateDestroyObjects" },
            new[] { "ZNetScene", "RemoveObjects" },
            new[] { "ZDOMan", "Update" },
            new[] { "ZDOMan", "ReleaseNearbyZDOS" },
            new[] { "ZoneSystem", "Update" },
            new[] { "ZoneSystem", "CreateLocalZones" },
            new[] { "ZoneSystem", "SpawnZone" },
            new[] { "ZoneSystem", "PokeLocalZone" },
            new[] { "Hud", "Update" },
            new[] { "Hud", "UpdateStatusEffects" },
            new[] { "Minimap", "Update" },
            new[] { "Minimap", "UpdateExplore" },
            new[] { "ClutterSystem", "Update" },
        };

        private static readonly Dictionary<MethodBase, Bucket> _buckets = new Dictionary<MethodBase, Bucket>();
        [ThreadStatic] private static Stack<long> _entered;

        private static bool _dead;
        private static int _installed;
        private static int _frames;
        private static float _windowStart;

        internal static void Install(Harmony harmony)
        {
            try
            {
                var prefix = new HarmonyMethod(typeof(GameMethodProfiler).GetMethod("Enter", BindingFlags.NonPublic | BindingFlags.Static));
                var postfix = new HarmonyMethod(typeof(GameMethodProfiler).GetMethod("Leave", BindingFlags.NonPublic | BindingFlags.Static));
                var skipped = new List<string>();
                foreach (var target in Targets)
                {
                    Type owner = TypeCache.Get(target[0]);
                    MethodInfo method = owner == null ? null : AccessTools.Method(owner, target[1]);
                    if (method == null) { skipped.Add(target[0] + "." + target[1]); continue; }
                    harmony.Patch(method, prefix, postfix);
                    _buckets[method] = new Bucket { Method = method };
                    _installed++;
                }
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "PERF instrument on " + _installed + " game methods"
                    + (skipped.Count > 0 ? "; absent in this build: " + string.Join(", ", skipped.ToArray()) : ""));
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "PERF instrument failed to install: " + e.Message);
            }
        }

        private static void Enter()
        {
            if (_dead) return;
            if (_entered == null) _entered = new Stack<long>();
            _entered.Push(Stopwatch.GetTimestamp());
        }

        private static void Leave(MethodBase __originalMethod)
        {
            if (_dead || _entered == null || _entered.Count == 0) return;
            long elapsed = Stopwatch.GetTimestamp() - _entered.Pop();
            Bucket bucket;
            if (!_buckets.TryGetValue(__originalMethod, out bucket)) return;
            bucket.Ticks += elapsed;
            bucket.Calls++;
        }

        internal static void Tick()
        {
            if (_dead || _installed == 0) return;
            try
            {
                _frames++;
                if (_windowStart <= 0f) _windowStart = Time.realtimeSinceStartup;
                float elapsed = Time.realtimeSinceStartup - _windowStart;
                if (elapsed < 15f) return;
                _windowStart = Time.realtimeSinceStartup;
                Report(elapsed);
                foreach (var bucket in _buckets.Values) { bucket.Ticks = 0; bucket.Calls = 0; }
                _frames = 0;
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "PERF instrument disabled: " + e.Message);
            }
        }

        private static void Report(float windowSeconds)
        {
            int frames = Math.Max(1, _frames);
            var rows = _buckets.Values
                .Where(b => b.Calls > 0)
                .Select(b => new { b.Method, Ms = b.Ticks * 1000.0 / Stopwatch.Frequency, b.Calls })
                .OrderByDescending(r => r.Ms)
                .ToList();

            double wall = windowSeconds * 1000.0;
            double measured = rows.Sum(r => r.Ms);
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "=== PERF game methods, " + frames + " frames over " + windowSeconds.ToString("F0") + "s, "
                + (wall / frames).ToString("F1") + " ms/frame wall, " + (measured / frames).ToString("F1")
                + " ms/frame measured here. Nested calls count in both rows; per-creature methods are "
                + "deliberately not instrumented ===");

            foreach (var row in rows)
            {
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "PERF " + row.Method.DeclaringType.Name + "." + row.Method.Name
                    + " " + (row.Ms / frames).ToString("F2") + " ms/frame"
                    + " calls=" + row.Calls
                    + " patchedBy=" + Owners(row.Method));
            }

            // The streaming backlog, next to the time spent creating objects, because a large
            // pending count with cheap CreateObjects means something else is holding the frame.
            try
            {
                if (ZNetScene.instance != null)
                {
                    var instances = AccessTools.Field(typeof(ZNetScene), "m_instances");
                    var pending = instances == null ? null : instances.GetValue(ZNetScene.instance) as System.Collections.ICollection;
                    if (pending != null)
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                            + "PERF world instances live=" + pending.Count);
                }
            }
            catch { }
        }

        private static string Owners(MethodBase method)
        {
            try
            {
                Patches info = Harmony.GetPatchInfo(method);
                if (info == null) return "nobody (the game's own cost)";
                var names = new List<string>();
                foreach (var patch in info.Prefixes) names.Add(patch.owner);
                foreach (var patch in info.Postfixes) names.Add(patch.owner);
                foreach (var patch in info.Transpilers) names.Add(patch.owner + "(transpiler)");
                foreach (var patch in info.Finalizers) names.Add(patch.owner + "(finalizer)");
                var others = names.Where(n => n != null && n.IndexOf("neuralyze", StringComparison.OrdinalIgnoreCase) < 0)
                                  .Distinct().ToArray();
                return others.Length == 0 ? "nobody (the game's own cost)" : string.Join(", ", others);
            }
            catch (Exception e) { return "<owners unreadable: " + e.Message + ">"; }
        }
    }
}
