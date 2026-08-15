using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using HarmonyLib;

namespace NeuralyzeVRFixes
{
    // Which mod makes the inventory slow.
    //
    // Measured on the player's own session: with the inventory on screen the frame p50 doubles,
    // 35ms to 60-77ms, while the compositor's GPU time stays flat at 27-35ms. So the cost is CPU
    // and it belongs to the panel, not to rendering. The per-mod profiler could not see it: it
    // wraps Update/LateUpdate/FixedUpdate only, and says so - a mod's Harmony patches on the game's
    // own methods are invisible to it. Fourteen installed mods patch InventoryGui.
    //
    // So this times the game's inventory methods themselves and names every mod patching each one.
    // A method that is expensive with no third-party patches on it is the game's own cost and no
    // mod removal will help; a method that is expensive with three mods on it is a shortlist.
    //
    // Nothing is divided between owners. Attributing 40ms three ways because three mods share a
    // method would invent a number for each of them; the honest output is the method's cost and the
    // list of who is attached to it.
    internal static class InventoryProfiler
    {
        private sealed class Bucket
        {
            internal long Ticks;
            internal long Calls;
            internal MethodBase Method;
        }

        // Only these run while the panel is on screen; taken from the game's own bytecode rather
        // than guessed, and every one is skipped silently if this build of Valheim lacks it.
        private static readonly string[][] Targets =
        {
            new[] { "InventoryGui", "Update" },
            new[] { "InventoryGui", "UpdateInventory" },
            new[] { "InventoryGui", "UpdateContainer" },
            new[] { "InventoryGui", "UpdateItemDrag" },
            new[] { "InventoryGui", "UpdateCharacterStats" },
            new[] { "InventoryGui", "UpdateInventoryWeight" },
            new[] { "InventoryGui", "UpdateContainerWeight" },
            new[] { "InventoryGui", "UpdateCraftingPanel" },
            new[] { "InventoryGui", "UpdateRecipeList" },
            new[] { "InventoryGui", "UpdateRecipe" },
            new[] { "InventoryGui", "SetupRequirementList" },
            new[] { "InventoryGui", "UpdateRepair" },
            new[] { "InventoryGui", "UpdateTrophyList" },
            new[] { "InventoryGui", "OnSelectedItem" },
            new[] { "InventoryGrid", "UpdateInventory" },
            new[] { "InventoryGrid", "UpdateGui" },
        };

        private static readonly Dictionary<MethodBase, Bucket> _buckets = new Dictionary<MethodBase, Bucket>();
        [ThreadStatic] private static Stack<long> _entered;

        private static bool _dead;
        private static bool _open;
        private static int _openFrames;
        private static float _windowStart;
        private static int _installed;

        internal static void Install(Harmony harmony)
        {
            try
            {
                var prefix = new HarmonyMethod(typeof(InventoryProfiler).GetMethod("Enter", BindingFlags.NonPublic | BindingFlags.Static));
                var postfix = new HarmonyMethod(typeof(InventoryProfiler).GetMethod("Leave", BindingFlags.NonPublic | BindingFlags.Static));
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
                    + "INV instrument on " + _installed + " inventory methods"
                    + (skipped.Count > 0 ? "; absent in this build: " + string.Join(", ", skipped.ToArray()) : ""));
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "INV instrument failed to install: " + e.Message);
            }
        }

        // The closed panel must cost nothing: one cached bool per frame, read here, no reflection
        // and no clock on the hot path when the inventory is not on screen.
        private static void Enter(MethodBase __originalMethod)
        {
            if (_dead || !_open) return;
            if (_entered == null) _entered = new Stack<long>();
            _entered.Push(Stopwatch.GetTimestamp());
        }

        private static void Leave(MethodBase __originalMethod)
        {
            if (_dead || !_open || _entered == null || _entered.Count == 0) return;
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
                _open = MenuContext.InventoryOnScreen();
                if (_open) _openFrames++;
                if (_windowStart <= 0f) _windowStart = UnityEngine.Time.realtimeSinceStartup;
                float elapsed = UnityEngine.Time.realtimeSinceStartup - _windowStart;
                if (elapsed < 10f) return;
                _windowStart = UnityEngine.Time.realtimeSinceStartup;
                if (_openFrames > 0) Report(elapsed);
                foreach (var bucket in _buckets.Values) { bucket.Ticks = 0; bucket.Calls = 0; }
                _openFrames = 0;
            }
            catch (Exception e)
            {
                _dead = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "INV instrument disabled: " + e.Message);
            }
        }

        private static void Report(float windowSeconds)
        {
            var rows = _buckets.Values
                .Where(b => b.Calls > 0)
                .Select(b => new { b.Method, Ms = b.Ticks * 1000.0 / Stopwatch.Frequency, b.Calls })
                .OrderByDescending(r => r.Ms)
                .ToList();
            double total = rows.Sum(r => r.Ms);

            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "=== INV inventory cost, " + _openFrames + " frames with the panel open over "
                + windowSeconds.ToString("F0") + "s; " + (total / Math.Max(1, _openFrames)).ToString("F2")
                + " ms/open-frame total. Nested calls are counted in both the outer and inner row ===");

            foreach (var row in rows)
            {
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "INV " + row.Method.DeclaringType.Name + "." + row.Method.Name
                    + " " + (row.Ms / Math.Max(1, _openFrames)).ToString("F2") + " ms/open-frame"
                    + " calls=" + row.Calls
                    + " patchedBy=" + Owners(row.Method));
            }
        }

        // Who else is attached to this method. A transpiler is named too: rewriting the body is a
        // way to make it slow that never shows up as a prefix.
        private static string Owners(MethodBase method)
        {
            try
            {
                Patches info = Harmony.GetPatchInfo(method);
                if (info == null) return "nobody";
                var names = new List<string>();
                foreach (var patch in info.Prefixes) names.Add(patch.owner);
                foreach (var patch in info.Postfixes) names.Add(patch.owner);
                foreach (var patch in info.Transpilers) names.Add(patch.owner + "(transpiler)");
                foreach (var patch in info.Finalizers) names.Add(patch.owner + "(finalizer)");
                var others = names.Where(n => n != null && n.IndexOf("neuralyze", StringComparison.OrdinalIgnoreCase) < 0)
                                  .Distinct().ToArray();
                return others.Length == 0 ? "nobody (this is the game's own cost)" : string.Join(", ", others);
            }
            catch (Exception e) { return "<owners unreadable: " + e.Message + ">"; }
        }
    }
}
