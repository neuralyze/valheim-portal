// LocScan - dump Valheim's LOCATION PLACEMENT for a seed, using the game's own
// ZoneSystem inside a real dedicated-server process.
//
// This is the companion to SeedScan.cs and answers a different question.
// SeedScan queries WorldGenerator, which knows only terrain: biomes and heights.
// Boss altars, traders, dungeons, runestones and the start temple are placed by
// ZoneSystem, which runs a completely separate rejection-sampling pass over
// zones and needs the game's location PREFABS loaded from the asset bundles.
// You cannot get them out of WorldGenerator at all.
//
// Why it needs a full server boot (unlike SeedScan):
//   * ZoneSystem.m_locations is populated by SetupLocations() from
//     m_locationLists / m_locationScenes - real Unity prefab data. That only
//     exists once the game scene has loaded.
//   * ZoneSystem.GenerateLocations() runs GenerateLocationsTimeSliced() as a
//     Unity COROUTINE, so it needs a live player loop over many frames.
// So this plugin lets the server boot normally, waits for
// ZoneSystem.instance.LocationsGenerated, dumps, and kills the process.
// One seed per process launch - about a minute each, versus SeedScan's ~1 s.
//
// Contract, all measured from assembly_valheim.dll (Valheim 1.0.12) IL:
//   World.GenerateSeed()                    -> static string, 10 chars from
//                                              "abcdefghijklmnpqrstuvwxyzABCDEFGHIJKLMNPQRSTUVWXYZ023456789"
//   World.GetCreateWorld(name, source)      -> calls GenerateSeed() when the
//                                              world does not exist yet, so a
//                                              Harmony postfix on GenerateSeed
//                                              is enough to force our seed.
//   ZoneSystem.instance                     -> static property
//   ZoneSystem.LocationsGenerated           -> public bool property
//   ZoneSystem.m_locationInstances          -> public Dictionary<Vector2s, LocationInstance>
//   ZoneSystem.LocationInstance             -> public fields m_location (ZoneLocation),
//                                              m_position (Vector3), m_placed (bool)
//   ZoneSystem.ZoneLocation                 -> public fields m_name, m_prefabName,
//                                              m_biome, m_quantity, m_unique, m_group,
//                                              m_iconAlways, m_iconPlaced,
//                                              m_minDistanceFromCenter, m_maxDistanceFromCenter
//   ZoneSystem.GetLocationIcons(Dictionary<Vector3,string>) -> the minimap icon set
//
// Env vars:
//   LOCSCAN_SEED      seed string to force (required)
//   LOCSCAN_OUT       output .json path (required)
//   LOCSCAN_TIMEOUT   seconds to wait for location generation (default 600)
// With LOCSCAN_OUT unset the plugin does nothing and the server boots normally.

using SIO = global::System.IO;
using SText = global::System.Text;
using SSys = global::System;
using SCG = global::System.Collections.Generic;

namespace SeedScan
{
    [BepInEx.BepInPlugin("vibeheim.locscan", "LocScan", "1.0.0")]
    public class LocScanPlugin : BepInEx.BaseUnityPlugin
    {
        static string s_forcedSeed;
        string _outPath;
        float _deadline;
        bool _done;

        void Awake()
        {
            _outPath = SSys.Environment.GetEnvironmentVariable("LOCSCAN_OUT");
            s_forcedSeed = SSys.Environment.GetEnvironmentVariable("LOCSCAN_SEED");
            if (string.IsNullOrEmpty(_outPath) || string.IsNullOrEmpty(s_forcedSeed))
            {
                SSys.Console.Error.WriteLine("[locscan] LOCSCAN_OUT / LOCSCAN_SEED not set; idle");
                return;
            }

            int timeout = 600;
            string t = SSys.Environment.GetEnvironmentVariable("LOCSCAN_TIMEOUT");
            if (!string.IsNullOrEmpty(t)) int.TryParse(t, out timeout);
            _deadline = UnityEngine.Time.realtimeSinceStartup + timeout;

            var harmony = new HarmonyLib.Harmony("vibeheim.locscan");
            harmony.Patch(
                typeof(World).GetMethod("GenerateSeed",
                    global::System.Reflection.BindingFlags.Public | global::System.Reflection.BindingFlags.Static),
                postfix: new HarmonyLib.HarmonyMethod(
                    typeof(LocScanPlugin).GetMethod(nameof(ForceSeed),
                        global::System.Reflection.BindingFlags.NonPublic | global::System.Reflection.BindingFlags.Static)));
            SSys.Console.Error.WriteLine("[locscan] forcing seed " + s_forcedSeed + ", timeout " + timeout + "s");
        }

        static void ForceSeed(ref string __result)
        {
            __result = s_forcedSeed;
            SSys.Console.Error.WriteLine("[locscan] World.GenerateSeed -> " + __result);
        }

        void Update()
        {
            if (_done || string.IsNullOrEmpty(_outPath)) return;

            ZoneSystem zs = ZoneSystem.instance;
            if (zs != null && zs.LocationsGenerated)
            {
                _done = true;
                try { Dump(zs); }
                catch (SSys.Exception e)
                {
                    SSys.Console.Error.WriteLine("[locscan] FATAL " + e.GetType().FullName + ": " + e.Message + "\n" + e.StackTrace);
                    Kill(3);
                }
                Kill(0);
            }
            else if (UnityEngine.Time.realtimeSinceStartup > _deadline)
            {
                _done = true;
                SSys.Console.Error.WriteLine("[locscan] TIMEOUT waiting for locations; zoneSystem="
                    + (zs == null ? "null" : "present, progress=" + zs.GenerateLocationsProgress));
                Kill(4);
            }
        }

        void Dump(ZoneSystem zs)
        {
            World w = ZNet.instance != null ? ZNet.instance.GetWorld() : null;
            var icons = new SCG.Dictionary<UnityEngine.Vector3, string>();
            zs.GetLocationIcons(icons);

            var sb = new SText.StringBuilder(1 << 20);
            sb.Append("{\n");
            sb.Append("  \"seed\": ").Append(Json(s_forcedSeed)).Append(",\n");
            sb.Append("  \"seedNameFromWorld\": ").Append(Json(w == null ? null : w.m_seedName)).Append(",\n");
            sb.Append("  \"hash\": ").Append(w == null ? 0 : w.m_seed).Append(",\n");
            sb.Append("  \"worldGenVersion\": ").Append(w == null ? -1 : w.m_worldGenVersion).Append(",\n");
            sb.Append("  \"locationsGenerated\": true,\n");

            int n = 0, placed = 0;
            sb.Append("  \"locations\": [");
            foreach (ZoneSystem.LocationInstance li in zs.m_locationInstances.Values)
            {
                if (n > 0) sb.Append(',');
                sb.Append("\n    {");
                sb.Append("\"name\": ").Append(Json(li.m_location == null ? null : li.m_location.m_name));
                sb.Append(", \"prefab\": ").Append(Json(li.m_location == null ? null : li.m_location.m_prefabName));
                sb.Append(", \"biome\": ").Append(Json(li.m_location == null ? null : li.m_location.m_biome.ToString()));
                sb.Append(", \"group\": ").Append(Json(li.m_location == null ? null : li.m_location.m_group));
                sb.Append(", \"unique\": ").Append(li.m_location != null && li.m_location.m_unique ? "true" : "false");
                sb.Append(", \"iconAlways\": ").Append(li.m_location != null && li.m_location.m_iconAlways ? "true" : "false");
                sb.Append(", \"iconPlaced\": ").Append(li.m_location != null && li.m_location.m_iconPlaced ? "true" : "false");
                sb.Append(", \"x\": ").Append(F(li.m_position.x));
                sb.Append(", \"y\": ").Append(F(li.m_position.y));
                sb.Append(", \"z\": ").Append(F(li.m_position.z));
                sb.Append(", \"placed\": ").Append(li.m_placed ? "true" : "false");
                sb.Append('}');
                n++;
                if (li.m_placed) placed++;
            }
            sb.Append("\n  ],\n");

            sb.Append("  \"icons\": [");
            int k = 0;
            foreach (var kv in icons)
            {
                if (k > 0) sb.Append(',');
                sb.Append("\n    {\"name\": ").Append(Json(kv.Value))
                  .Append(", \"x\": ").Append(F(kv.Key.x))
                  .Append(", \"y\": ").Append(F(kv.Key.y))
                  .Append(", \"z\": ").Append(F(kv.Key.z)).Append('}');
                k++;
            }
            sb.Append("\n  ]\n}\n");

            SIO.Directory.CreateDirectory(SIO.Path.GetDirectoryName(SIO.Path.GetFullPath(_outPath)));
            SIO.File.WriteAllText(_outPath, sb.ToString());
            SSys.Console.Error.WriteLine("[locscan] wrote " + _outPath + ": " + n + " locations ("
                + placed + " placed), " + k + " icons, seed=" + (w == null ? "?" : w.m_seedName)
                + " hash=" + (w == null ? 0 : w.m_seed));
        }

        static string F(float v)
        {
            return v.ToString("0.##", global::System.Globalization.CultureInfo.InvariantCulture);
        }

        static string Json(string s)
        {
            if (s == null) return "null";
            var sb = new SText.StringBuilder(s.Length + 2);
            sb.Append('"');
            foreach (char c in s)
            {
                if (c == '"' || c == '\\') sb.Append('\\').Append(c);
                else if (c == '\n') sb.Append("\\n");
                else if (c == '\r') sb.Append("\\r");
                else if (c == '\t') sb.Append("\\t");
                else if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            sb.Append('"');
            return sb.ToString();
        }

        // Same reason as SeedScan: Unity's shutdown path deadlocks when driven
        // from plugin code in -batchmode. Outputs are flushed before this runs.
        static void Kill(int unused)
        {
            SSys.Console.Error.Flush();
            global::System.Diagnostics.Process.GetCurrentProcess().Kill();
        }
    }
}
