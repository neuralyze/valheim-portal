using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using BepInEx;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace ValheimDiagnostics
{
    // Read-only diagnostics for the world-load path. This plugin deliberately
    // changes nothing: an earlier revision re-enabled ZNetScene when it found the
    // component disabled, which masked the real fault (a mod throwing inside the
    // ZNetScene.Awake postfix chain) instead of naming it. Reporting the disabled
    // state alongside the patch owners identifies the culprit without hiding it.
    [BepInPlugin(GUID, "Valheim Diagnostics", Version)]
    public class ValheimDiagnosticsPlugin : BaseUnityPlugin
    {
        public const string GUID = "neuralyze.valheimdiagnostics";
        public const string Version = "1.7.0";
        private const string Tag = "[ValheimDiagnostics] ";
        private const float SampleSeconds = 2f;

        internal static ManualLogSource Log;
        private static readonly FieldInfo F_instances = AccessTools.Field(typeof(ZNetScene), "m_instances");

        internal static long nUpdate, nCreateDestroy, nAddInstance;
        internal static string awakeState = "(Awake not seen)";
        private static bool _dumped;
        private static bool _reportedDisabled;
        private float _next;
        private HudWatch _hud;
        private int _errors;

        private void Awake()
        {
            Log = Logger;
            _hud = new HudWatch(Logger);
            Log.LogInfo(Tag + "loaded " + Version + " (read-only; patches nothing)");
            Harmony harmony = new Harmony(GUID);
            try { harmony.PatchAll(typeof(ValheimDiagnosticsPlugin).Assembly); }
            catch (Exception e) { Log.LogWarning(Tag + "patch install failed: " + e.Message); }
            // Installed separately and reflectively: the target lives in ValheimVRMod,
            // which this assembly is deliberately not compiled against.
            PanelWatch.Install(harmony, Logger);
        }

        // Names every mod patching the methods that govern object creation, in the
        // order Harmony runs them. A postfix that throws aborts every postfix
        // ordered after it, so this ordering is what identifies an owner.
        internal static void DumpPatchOwners()
        {
            if (_dumped) return;
            _dumped = true;
            string[] targets = { "Awake", "Update", "CreateDestroyObjects", "CreateObjects", "CreateObject" };
            foreach (string name in targets)
            {
                try
                {
                    MethodInfo target = AccessTools.Method(typeof(ZNetScene), name);
                    if (target == null) { Log.LogInfo(Tag + "PATCHES ZNetScene." + name + " -> method not found"); continue; }
                    var info = Harmony.GetPatchInfo(target);
                    if (info == null) { Log.LogInfo(Tag + "PATCHES ZNetScene." + name + " -> unpatched"); continue; }
                    Log.LogInfo(Tag + "PATCHES ZNetScene." + name
                        + " prefixes=[" + Describe(info.Prefixes) + "]"
                        + " postfixes=[" + Describe(info.Postfixes) + "]"
                        + " finalizers=[" + Describe(info.Finalizers) + "]"
                        + " transpilers=[" + Describe(info.Transpilers) + "]");
                }
                catch (Exception e) { Log.LogWarning(Tag + "patch dump failed for " + name + ": " + e.Message); }
            }
        }

        private static string Describe(IList<Patch> patches)
        {
            if (patches == null || patches.Count == 0) return "";
            List<Patch> ordered = new List<Patch>(patches);
            ordered.Sort(delegate (Patch a, Patch b) { return b.priority.CompareTo(a.priority); });
            List<string> parts = new List<string>();
            foreach (Patch patch in ordered)
                parts.Add(patch.owner + "(p" + patch.priority + ")");
            return string.Join(" -> ", parts.ToArray());
        }

        private static int CountOf(FieldInfo f, object t)
        { try { ICollection c = (f != null && t != null ? f.GetValue(t) : null) as ICollection; return c != null ? c.Count : -1; } catch { return -1; } }

        private void Update()
        {
            // Per-frame, because the artifact under investigation is per-frame. A
            // once-per-second sample cannot see a one-frame jitter.
            try { if (_hud != null) _hud.Sample(); }
            catch (Exception e) { if (_errors++ < 3) Log.LogWarning(Tag + "hud sample failed: " + e.Message); }
            if (Time.unscaledTime < _next) return;
            _next = Time.unscaledTime + SampleSeconds;
            try { Sample(ZNetScene.instance); }
            catch (Exception e) { if (_errors++ < 5) Log.LogWarning(Tag + "sample failed: " + e); }
        }

        private void Sample(ZNetScene scene)
        {
            if (scene == null) return;
            DumpPatchOwners();
            Log.LogInfo(Tag + "AWAKE-STATE " + awakeState);

            // The component being disabled stops Update, which stops object
            // creation entirely. Report it once, loudly, and leave it alone.
            if (!scene.enabled && !_reportedDisabled)
            {
                _reportedDisabled = true;
                Log.LogError(Tag + "ZNetScene IS DISABLED - no objects will ever be created. "
                    + "A mod disabled the component; see the PATCHES lines above for the owners in execution order.");
            }

            if (Player.m_localPlayer != null) { Log.LogInfo(Tag + "PLAYER SPAWNED - world load completed"); return; }
            Game game = Game.instance; ZoneSystem zones = ZoneSystem.instance;
            if (game == null || zones == null) return;

            Vector3 spawn; bool g1 = zones.GetLocationIcon(game.m_StartLocation, out spawn);
            Vector2i zone = ZoneSystem.GetZone(spawn);
            bool g3 = scene.IsAreaReady(spawn);
            Log.LogInfo(string.Format(Tag + "enabled={0} G1={1} G2={2} G3={3} | Update={4} CreateDestroy={5} AddInstance={6} instances={7} timeScale={8} deltaTime={9:0.0000}",
                scene.enabled, g1, zones.IsZoneLoaded(zone), g3, nUpdate, nCreateDestroy, nAddInstance,
                CountOf(F_instances, scene), Time.timeScale, Time.deltaTime));
            if (g3) return;

            try
            {
                List<ZDO> sector = new List<ZDO>();
                ZDOMan.instance.FindSectorObjects(zone, 1, 0, sector, null);
                Dictionary<string, int> unresolved = new Dictionary<string, int>();
                Dictionary<string, int> resolved = new Dictionary<string, int>();
                int missing = 0;
                for (int i = 0; i < sector.Count; i++)
                {
                    ZDO zdo = sector[i];
                    if (scene.FindInstance(zdo) != null) continue;
                    missing++;
                    int hash = zdo.GetPrefab();
                    GameObject pf = null;
                    try { pf = scene.GetPrefab(hash); } catch { }
                    string key = pf != null ? pf.name : ("MISSING_PREFAB#" + hash);
                    Dictionary<string, int> bucket = pf != null ? resolved : unresolved;
                    int n; bucket.TryGetValue(key, out n); bucket[key] = n + 1;
                }
                Log.LogInfo(string.Format(Tag + "  sectorZDOs={0} missingInstances={1}", sector.Count, missing));
                // Prefabs the server placed but this client cannot resolve mean a
                // mod mismatch or an asset that failed to load on the client.
                if (unresolved.Count > 0) Log.LogError(Tag + "  PREFAB NOT ON CLIENT -> " + Join(unresolved));
                if (resolved.Count > 0) Log.LogInfo(Tag + "  resolvable but not instantiated -> " + Join(resolved));
            }
            catch (Exception e) { Log.LogWarning(Tag + "  sector inspect failed: " + e.Message); }
        }

        private static string Join(Dictionary<string, int> d)
        {
            List<string> parts = new List<string>();
            foreach (KeyValuePair<string, int> kv in d) parts.Add(kv.Key + " x" + kv.Value);
            parts.Sort();
            if (parts.Count > 14) parts.RemoveRange(14, parts.Count - 14);
            return string.Join(", ", parts.ToArray());
        }
    }

    // Records whether ZNetScene is already disabled before and after its own Awake,
    // which separates "arrived disabled" from "a mod disabled it during Awake". The
    // postfix runs at Priority.Last, so its absence from the log also proves that
    // an earlier postfix in the chain threw.
    [HarmonyPatch(typeof(ZNetScene), "Awake")]
    static class P_Awake
    {
        static void Prefix(ZNetScene __instance)
        {
            try { ValheimDiagnosticsPlugin.awakeState = "prefix.enabled=" + (__instance != null && __instance.enabled); }
            catch { }
        }

        [HarmonyPriority(Priority.Last)]
        static void Postfix(ZNetScene __instance)
        {
            try { ValheimDiagnosticsPlugin.awakeState += " postfix.enabled=" + (__instance != null && __instance.enabled) + " frame=" + Time.frameCount; }
            catch { }
        }
    }

    [HarmonyPatch(typeof(ZNetScene), "Update")]
    static class P_Update { static void Postfix() { ValheimDiagnosticsPlugin.nUpdate++; } }

    [HarmonyPatch(typeof(ZNetScene), "CreateDestroyObjects")]
    static class P_CreateDestroy { static void Postfix() { ValheimDiagnosticsPlugin.nCreateDestroy++; } }

    [HarmonyPatch(typeof(ZNetScene), "AddInstance")]
    static class P_AddInstance { static void Postfix() { ValheimDiagnosticsPlugin.nAddInstance++; } }
}
