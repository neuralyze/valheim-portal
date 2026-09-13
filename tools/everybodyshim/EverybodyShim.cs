// EverybodyShim - a BepInEx preloader patcher that turns ZRoutedRpc.Everybody
// back into a real runtime field on Valheim 1.0.12.
//
// ---------------------------------------------------------------------------
// Diagnosis, 2026-09-12
// ---------------------------------------------------------------------------
// Valheim 1.0.12 changed `ZRoutedRpc.Everybody` from a plain static field into
// a `const`. Measured against the live 1.0 server assembly
// (docker cp valheim-server-Ulfsland:/opt/valheim/server/valheim_server_Data/Managed/assembly_valheim.dll,
// 2560000 bytes):
//
//   monodis --typedef   -> 317: ZRoutedRpc (flist=4777, ...)
//   monodis --fields    -> 4777: int64 Everybody: public static literal
//   monodis --constant  -> 543: Parent= Field: 4777 int64(0x0000000000000000)
//
// `literal` is a compile-time constant. It has no runtime storage slot, so the
// `ldsfld int64 ZRoutedRpc::Everybody` that older mods emit cannot resolve it.
// Mono reports exactly that:
//
//   MissingFieldException: Field not found: .ZRoutedRpc.Everybody
//   Due to: Using static instructions with literal field
//     at ServerSync.CustomSyncedValueBase.set_BoxedValue
//     at ServerSync.CustomSyncedValue`1.set_Value
//     at ServerSync.CustomSyncedValue`1..ctor
//     at <mod>.Awake()
//
// ServerSync is ILRepacked into each mod that uses it, so there is one copy of
// the broken `ldsfld` per mod rather than one shared library to fix. A scan of
// every DLL under
// /media/big4/projects/game/valheim/profiles/ulfsland-dn/manager-cache/server/BepInEx/plugins
// with `monodis --memberref`, intersected against every `literal` field in
// assembly_valheim, found exactly one const-ified game field referenced by the
// plugin set - ZRoutedRpc.Everybody - referenced by 41 mods. Nothing else in
// the 1716 literal fields of the game assembly is touched by memberref from a
// plugin, so this one field is the whole defect surface.
//
// ---------------------------------------------------------------------------
// Why converting the const back to a field is safe
// ---------------------------------------------------------------------------
// A const is inlined at the callsite, so shipped 1.0 code never reads it
// through `ldsfld`. Verified on the 1.0 assembly:
//
//   monodis --output=av.il assembly_valheim.dll     (581271 lines)
//   grep -c 'ldsfld.*ZRoutedRpc::Everybody' av.il   -> 0
//   grep -c 'ZRoutedRpc::Everybody'         av.il   -> 0   (no opcode at all)
//   control: grep -c 'ldsfld' av.il                 -> 5721
//   control: grep -c 'ldsfld.*ZNetView::Everybody'  -> 50
//
// The control matters: `ZNetView.Everybody` (field 4752, `public static`, not
// literal) is a different and still-normal field with the same member name, and
// the search does find 50 `ldsfld` reads of it. So the zero for ZRoutedRpc is a
// real absence, not a broken pattern.
//
// ---------------------------------------------------------------------------
// Why a preloader patcher rather than rewriting the mods
// ---------------------------------------------------------------------------
// The portal ships mod DLLs to players through profile sync. Rewriting 41
// third-party binaries - some under licences that constrain redistribution of
// modified copies - and re-applying the rewrite after every mod update is both
// a licence problem and a permanent maintenance tax. This patcher rewrites the
// GAME assembly in memory at load time and redistributes nothing.
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
// The server image rsyncs /config/bepinex/plugins into BepInEx/plugins but has
// no equivalent for patchers (write_bepinex_config in valheim-server-docker's
// common syncs plugins only), so this DLL is installed directly into the live
// tree, which is a host bind mount:
//
//   <world>/data/bepinex/BepInEx/patchers/EverybodyShim.dll
//     -> /opt/valheim/bepinex/BepInEx/patchers/ in the container
//
// That survives container stop/start and recreation. It does NOT survive a
// BepInEx or server update, because merge_valheim_and_mod rebuilds
// /opt/valheim/bepinex from scratch into a fresh .tmp directory. Reinstall
// after any update that reports a BepInEx merge. Making it survive would take
// a POST_BEPINEX_CONFIG_HOOK that copies it back, which lives in the shared
// server-docker config rather than here.
//
// ---------------------------------------------------------------------------
// Measured result, Ulfsland, 2026-09-12
// ---------------------------------------------------------------------------
// Two 7-minute boots on the same rebuilt image with the same 100-plugin set,
// differing only in whether this DLL was in patchers/:
//
//   without: 12x "MissingFieldException: Field not found: .ZRoutedRpc.Everybody
//            Due to: Using static instructions with literal field", and seven
//            dead type initializers - AzuAutoStore, AzuCraftyBoxes, Backpacks,
//            BlacksmithingExpanded, CreatureLevelControl, JudesEquipment,
//            ItemDataManager.
//   with:    0 of those MissingFieldExceptions; six of the seven initializers
//            run. Only ItemDataManager still fails, for an unrelated reason -
//            Harmony cannot find Inventory.AddItem(ItemData,int,int,int)
//            because 1.0 appended an optional fifth parameter.
//
// BepInEx logs the patcher by name at Info level:
//   [Info   :   BepInEx] Loaded 1 patcher method from [EverybodyShim 0.0.0.0]
//   [Info   :   BepInEx] Patching [assembly_valheim] with [Neuralyze.EverybodyShim.EverybodyShim]
//   [Info   :EverybodyShim] ZRoutedRpc.Everybody converted from const (0) to
//                          public static field; ldsfld now resolves.
// and with Preloader.DumpAssemblies=true the assembly it actually loaded
// disassembles to ".field public static int64 Everybody" plus a generated
// .cctor of "ldc.i8 0x0; stsfld int64 ZRoutedRpc::Everybody; ret".
//
// Adding the forwarding overloads of AppendedOptionalForwards.cs to the same
// server, same window, took the modded boot the rest of the way:
//
//   const fix only:  17668 error lines; 8756 Character.Message and 8756
//                    SEMan.AddStatusEffect MissingMethodExceptions, one pair
//                    per frame from "Activating first scene!" onward; the
//                    server never left the start scene.
//   + forwards:      260 error lines; both floods zero.
//   + forwards, with shudnal-HarpoonExtended disabled:
//                    "isModded: True", "Zonesystem Awake", "Zonesystem Start",
//                    "DungeonDB Start", "ZRpc timeout set to 30s" - a fully
//                    loaded modded world for the first time on 1.0.12.
//
// HarpoonExtended was the last blocker and is not shimmable: its own Postfix
// on ObjectDB.Awake/CopyOtherDB throws NullReferenceException inside
// FejdStartup.SetupObjectDB, which aborts the path that starts the world. It
// needs fixing or replacing, not patching around.

using System;
using System.Collections.Generic;
using BepInEx.Logging;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Neuralyze.EverybodyShim
{
    public class EverybodyShim
    {
        private const string TargetType = "ZRoutedRpc";
        private const string TargetField = "Everybody";

        private static readonly ManualLogSource Log =
            Logger.CreateLogSource("EverybodyShim");

        public static IEnumerable<string> TargetDLLs
        {
            get { yield return "assembly_valheim.dll"; }
        }

        public static void Initialize()
        {
            Log.LogInfo("EverybodyShim loaded: de-literalize ZRoutedRpc.Everybody, plus "
                + AppendedOptionalForwards.Table.Length
                + " forwarding overloads for methods that gained an appended optional parameter.");
        }

        public static void Patch(AssemblyDefinition assembly)
        {
            DeLiteralizeEverybody(assembly);

            int emitted = AppendedOptionalForwards.Apply(assembly, Log);
            Log.LogInfo(string.Format("{0} of {1} forwarding overloads emitted.",
                emitted, AppendedOptionalForwards.Table.Length));
        }

        private static void DeLiteralizeEverybody(AssemblyDefinition assembly)
        {
            TypeDefinition type = assembly.MainModule.GetType(TargetType);
            if (type == null)
            {
                Log.LogWarning("Type " + TargetType + " not found; field left untouched.");
                return;
            }

            FieldDefinition field = null;
            foreach (FieldDefinition candidate in type.Fields)
            {
                if (candidate.Name == TargetField)
                {
                    field = candidate;
                    break;
                }
            }

            if (field == null)
            {
                Log.LogWarning(TargetType + "." + TargetField + " not found; field left untouched.");
                return;
            }

            if (!field.IsLiteral)
            {
                // Either a game build that never const-ified it, or a second
                // pass over an already-patched definition. Nothing to do.
                Log.LogInfo(TargetType + "." + TargetField + " is already a runtime field; no change needed.");
                return;
            }

            object priorValue = field.Constant;

            // A literal has no runtime slot. Clearing Literal/HasDefault and
            // dropping the Constant row is what gives it one; Static/Public are
            // asserted rather than assumed so the resulting field is exactly
            // what pre-1.0.12 mods were compiled against.
            field.Constant = null;
            field.HasConstant = false;
            field.IsLiteral = false;
            field.HasDefault = false;
            field.IsStatic = true;
            field.IsPublic = true;

            EnsureZeroInStaticConstructor(assembly, type, field);

            Log.LogInfo(string.Format(
                "{0}.{1} converted from const ({2}) to public static field; ldsfld now resolves.",
                TargetType, TargetField, priorValue == null ? "null" : priorValue.ToString()));
        }

        // The CLI zero-initializes static fields, so a 0-valued Int64 field is
        // already 0 without any initializer. The explicit store is belt and
        // braces: it makes the shipped value visible in the rewritten IL and
        // survives any future change to the const's value, which would then be
        // written here instead of silently defaulting.
        private static void EnsureZeroInStaticConstructor(
            AssemblyDefinition assembly, TypeDefinition type, FieldDefinition field)
        {
            MethodDefinition cctor = null;
            foreach (MethodDefinition method in type.Methods)
            {
                if (method.IsStatic && method.IsConstructor)
                {
                    cctor = method;
                    break;
                }
            }

            if (cctor == null)
            {
                // ZRoutedRpc has no static constructor in 1.0.12 (monodis over
                // the class shows eight fields and no .cctor), so this is the
                // path that runs.
                cctor = new MethodDefinition(
                    ".cctor",
                    MethodAttributes.Private | MethodAttributes.HideBySig |
                    MethodAttributes.SpecialName | MethodAttributes.RTSpecialName |
                    MethodAttributes.Static,
                    assembly.MainModule.TypeSystem.Void);
                cctor.Body.GetILProcessor().Append(Instruction.Create(OpCodes.Ret));
                type.Methods.Add(cctor);
            }

            ILProcessor il = cctor.Body.GetILProcessor();
            Instruction first = cctor.Body.Instructions[0];
            il.InsertBefore(first, Instruction.Create(OpCodes.Ldc_I8, 0L));
            il.InsertBefore(first, Instruction.Create(OpCodes.Stsfld, field));
        }
    }
}
