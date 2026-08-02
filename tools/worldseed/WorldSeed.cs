using System;
using System.IO;
using BepInEx;
using HarmonyLib;

namespace NeuralyzeWorldSeed
{
    // Forces the seed used when Valheim CREATES a world, so a world can be regenerated from a chosen
    // seed through the game's own code path.
    //
    // Why this exists: a dedicated server has no seed argument, and the seed lives only in the .fwl.
    // World.CheckDbFile()/LoadWorld reject a .fwl whose .db is missing, so deleting the .db to reset a
    // world makes Valheim treat it as absent and call World.GenerateSeed() for a NEW random seed. That
    // silently replaced a live world twice here: once when its .db went missing unnoticed, and again
    // during a reset attempt that kept only the .fwl. Each time the original seed was overwritten.
    //
    // Hand-pairing an old .fwl with a foreign .db avoids the rename but leaves zones marked generated
    // under the wrong seed. Patching GenerateSeed instead lets the game build a genuinely fresh world:
    // correct seed, empty database, nothing stale.
    //
    // It is also a permanent safety net. Left installed, any future accidental regeneration recreates
    // THIS world rather than a random one.
    [BepInPlugin("neuralyze.worldseed", "Neuralyze World Seed", "1.0.0")]
    public class WorldSeedPlugin : BaseUnityPlugin
    {
        internal static string Seed;
        internal static ManualLogSourceShim Log;

        private void Awake()
        {
            Log = new ManualLogSourceShim(Logger);

            // Config first so it is visible and editable; environment variable overrides it, which is
            // how the container passes per-world values.
            var entry = Config.Bind("World", "ForcedSeedName", "",
                "Seed NAME to use whenever Valheim creates this world. Empty disables the patch. " +
                "Only affects creation - an existing world is never touched. Overridden by " +
                "VALHEIM_FORCED_SEED_NAME if that is set.");
            string fromEnv = Environment.GetEnvironmentVariable("VALHEIM_FORCED_SEED_NAME");
            Seed = string.IsNullOrEmpty(fromEnv) ? entry.Value : fromEnv;

            if (string.IsNullOrEmpty(Seed))
            {
                Logger.LogInfo("no forced seed configured; world creation left alone");
                return;
            }
            new Harmony("neuralyze.worldseed").PatchAll(typeof(WorldSeedPlugin).Assembly);
            Logger.LogWarning("world creation will use forced seed name '" + Seed + "'");
        }
    }

    // Tiny indirection so the patch class can log without holding a BepInEx type reference.
    internal class ManualLogSourceShim
    {
        private readonly BepInEx.Logging.ManualLogSource _inner;
        internal ManualLogSourceShim(BepInEx.Logging.ManualLogSource inner) { _inner = inner; }
        internal void Warn(string message) { if (_inner != null) _inner.LogWarning(message); }
    }

    [HarmonyPatch(typeof(World), "GenerateSeed")]
    internal static class ForceSeedName
    {
        // Skips the original entirely: GenerateSeed is called only when a world is being created, so
        // there is no path where overriding it can disturb an existing save.
        private static bool Prefix(ref string __result)
        {
            if (string.IsNullOrEmpty(WorldSeedPlugin.Seed)) return true;
            __result = WorldSeedPlugin.Seed;
            if (WorldSeedPlugin.Log != null)
                WorldSeedPlugin.Log.Warn("World.GenerateSeed intercepted -> '" + __result + "'");
            return false;
        }
    }
}
