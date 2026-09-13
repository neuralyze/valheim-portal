// EverybodyShim - a BepInEx preloader patcher holding the one Valheim 1.0
// compatibility bridge that Wubarrk-Valheim10Compatibility refuses to emit.
//
// ---------------------------------------------------------------------------
// What this is now, and why it shrank - 2026-09-13
// ---------------------------------------------------------------------------
// This started on 2026-09-12 as six bridges of our own, built before anything
// comparable existed: ZRoutedRpc.Everybody de-literalized from a const back to
// a static field, plus forwarding overloads for Character.Message,
// SEMan.AddStatusEffect (both overloads), Inventory.AddItem and
// EffectList.Create. Five of those six are now DELETED. Wubarrk-Valheim10-
// Compatibility 1.4.0 does all of them, does them better, and does one thing
// we never could: it detours HarmonyX's AccessTools lookups so by-name
// [HarmonyPatch] targets resolve past both its bridges AND vanilla's own new
// 1.0 overloads. Ours had no answer for that, and our own Character.Message
// overload was actively harmful beside it - see the measurement below.
//
// The name is historical. It no longer touches ZRoutedRpc.Everybody; that
// bridge is Valheim10Compatibility's now. The name is kept because the DLL is
// installed under it on every server and renaming buys nothing.
//
// ---------------------------------------------------------------------------
// The one thing still here: ZDO.GetSector() -> Vector2i
// ---------------------------------------------------------------------------
// Valheim10Compatibility deliberately BLOCKS return-type-only overloads, and
// logs why:
//
//   BLOCKED ZDO.GetSector() -> Vector2i: this would differ from the existing
//   Vector2s GetSector(...) by RETURN TYPE ALONE. Type.GetMethod cannot
//   disambiguate that, so every plugin reflecting on this name would throw
//   AmbiguousMatchException at startup. Left unbridged deliberately; pre-1.0
//   call sites to it stay broken.
//
// Sound in principle, and not what happens on our profile. Measured twice on
// the 94-plugin ulfsland-dn set: emitting that overload produced ZERO plugin
// AmbiguousMatchException. Every residual "Ambiguous" line in those boots was
// either a Jotunn "Ambiguous asset name for path" warning or the text of
// Valheim10Compatibility's own BLOCKED message - checked line by line, not by
// count. Their policy costs 7774 MissingMethodExceptions a boot here, from
// CreatureLevelAndLootControl, Serverside_Simulations and ServersideQoL.
//
// So we keep exactly the bridge they will not make, and nothing else.
//
// ---------------------------------------------------------------------------
// The measurement that decided it - Ulfsland, four boots, one variable each
// ---------------------------------------------------------------------------
// Same rebuilt image, same 94-plugin deployed set, ~7 minutes each. All four
// reached isModded: True / Zonesystem Awake / DungeonDB Start.
//
//   ours alone (all six bridges)          89 error lines   AzuAreaRepair dead
//   Valheim10Compatibility alone        7817 error lines   7774 of them GetSector
//   both, ours unmodified                 75 error lines   AzuAreaRepair dead
//   theirs + ours cut to GetSector        41 error lines   nothing ambiguous
//
// The third row is the one that justifies the deletions rather than merely
// suggesting them: their Harmony resolver cannot see an overload WE injected,
// so our Character.Message forward re-broke AzuAreaRepair.PlayerRepairTrans-
// piler, the very plugin their hook had just repaired. Our forwards were not
// redundant beside theirs, they were harmful. In the fourth configuration
// AzuAreaRepair loads and registers its ConfigSync RPC, and ItemDataManager
// stops failing too.
//
// Load order stops mattering under this arrangement. BepInEx runs patchers in
// sorted order of type full name - AssemblyPatcher.AddPatchersFromDirectory
// builds a SortedDictionary<string, PatcherPlugin> keyed on TypeName, verified
// in BepInEx.Preloader.dll 5.4.23.3 IL - so ours ran first when it had bridges
// that overlapped theirs. Now the only bridge left is one they refuse
// outright, so neither order can collide. Both patchers are idempotent
// regardless: theirs logs "present natively, no bridge needed" when it finds
// the target already there, which is exactly what it did to this bridge in the
// both-together run.
//
// ---------------------------------------------------------------------------
// Patcher contract, read out of BepInEx.Preloader.dll 5.4.23.3 on this install
// ---------------------------------------------------------------------------
// AssemblyPatcher.ToPatcherPlugin rejects interfaces and (abstract && sealed)
// types - so a C# `static class` is NOT accepted; this must be a plain class
// with static members. It then requires:
//   - `get_TargetDLLs`, public static, returning
//     "System.Collections.Generic.IEnumerable`1<System.String>"
//   - `Patch`, public static, returning "System.Void", with exactly one
//     parameter of "Mono.Cecil.AssemblyDefinition" or
//     "Mono.Cecil.AssemblyDefinition&"
// and optionally binds `Initialize()` and `Finish()`, both void and nullary.
//
// ---------------------------------------------------------------------------
// Deployment, and the one way it can be lost
// ---------------------------------------------------------------------------
// The durable source directory is the world's own config tree, which the
// server image rsyncs into the install on every start:
//
//   <world>/config_merged/bepinex/patchers/EverybodyShim.dll
//     -> /config/bepinex/patchers/ -> <install>/BepInEx/patchers/
//
// Proof it is live is the boot line "Syncing BepInEx patchers from
// /config/bepinex/patchers/ -> ...". Installing only into
// <world>/data/bepinex/BepInEx/patchers/ also works and takes effect without a
// restart, but does NOT survive a BepInEx or server update, because
// merge_valheim_and_mod rebuilds that tree from scratch.

using System;
using System.Collections.Generic;
using BepInEx.Logging;
using Mono.Cecil;

namespace Neuralyze.EverybodyShim
{
    public class EverybodyShim
    {
        private static readonly ManualLogSource Log =
            Logger.CreateLogSource("EverybodyShim");

        public static IEnumerable<string> TargetDLLs
        {
            get { yield return "assembly_valheim.dll"; }
        }

        public static void Initialize()
        {
            Log.LogInfo(string.Format(
                "EverybodyShim loaded: {0} appended-optional forwards, {1} return-type forwards. "
                + "Everything else is delegated to Valheim10Compatibility.",
                AppendedOptionalForwards.Table.Length, ReturnTypeWidenedForwards.Table.Length));
        }

        public static void Patch(AssemblyDefinition assembly)
        {
            int emitted = AppendedOptionalForwards.Apply(assembly, Log);
            if (AppendedOptionalForwards.Table.Length > 0)
            {
                Log.LogInfo(string.Format("{0} of {1} appended-optional forwards emitted.",
                    emitted, AppendedOptionalForwards.Table.Length));
            }

            int widened = ReturnTypeWidenedForwards.Apply(assembly, Log);
            Log.LogInfo(string.Format("{0} of {1} return-type forwards emitted.",
                widened, ReturnTypeWidenedForwards.Table.Length));
        }
    }
}
