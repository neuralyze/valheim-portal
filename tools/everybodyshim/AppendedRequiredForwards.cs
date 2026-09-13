// A FOURTH defect shape: 1.0 appended a parameter that is REQUIRED and carries no
// .param default, so the game's own metadata does not say what the old call meant.
//
// AppendedOptionalForwards refuses this class on purpose, and that refusal is
// load-bearing - it is what automatically rejects Humanoid.IsTeleportable,
// VisEquipment.SetLeftItem and the rest, where no value can be defended. This emitter
// does NOT relax that rule. It is a separate table whose every row has to carry a proof,
// read out of the disassembly, that ONE value reproduces the pre-1.0 observable
// behaviour. Absent that proof a row does not belong here either.
//
// The bar, stated so a later row cannot be added casually: it is not enough that a value
// looks sensible or is the type's default. The old method's behaviour must be shown to be
// IDENTICAL to the new method called with the chosen value. "Probably harmless" is the
// language of an invention.
//
// ROW 1 - CookingStation.SpawnItem(string, int32, Vector3)
//
//   1.0.12:   instance void SpawnItem(string name, int32 slot,
//                                     Vector3 userPoint, bool cheated)
//   pre-1.0:  instance void SpawnItem(string name, int32 slot, Vector3 userPoint)
//
//   `cheated` has no .param entry, so IsAppendedOptionalForm rejects it.
//
//   Its ONLY use in the 1.0 body:
//     IL_00a0:  ldloc.3
//     IL_00a1:  ldarg.s 4
//     IL_00a3:  call void ItemDrop::OnCreateNew(ItemDrop, bool)
//
//   and OnCreateNew's whole body is two stores:
//     IL_000c:  stfld int32 ItemDrop/ItemData::m_worldLevel
//     IL_0018:  stfld bool  ItemDrop/ItemData::m_cheated
//
//   The pre-1.0 SpawnItem body contains NO call to OnCreateNew at all - grep it - so a
//   cooking station never marked its output cheated and m_cheated stayed at its default
//   false. Passing false therefore RESTATES the old outcome; passing true would invent a
//   cheat marking that no 0.220 server ever produced. That is the proof this table
//   demands, and it is why this row is admissible while VisEquipment.SetRightItem - whose
//   appended parameter is an item NAME with no defensible value - is not.
//
//   Reproduce:
//     cd <Managed> && MONO_PATH=. monodis --output=av.il assembly_valheim.dll
//     grep -n "SpawnItem (string" av.il     # then read the body and OnCreateNew
//
//   What it fixes: CreatureLevelAndLootControl transpiles CookingStation.RPC_RemoveDoneItem
//   and emits a call to the 3-parameter SpawnItem. Without the forward that call does not
//   resolve and Harmony reports "InvalidProgramException: Invalid IL code in
//   CookingStation:DMD<CookingStation::RPC_RemoveDoneItem>" plus an IL Compile Error,
//   measured as 3 of the 13 remaining error lines on Ulfsland 2026-09-13.
using System;
using System.Collections.Generic;
using BepInEx.Logging;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Neuralyze.EverybodyShim
{
    /// One row: the short signature a pre-1.0 mod emits, plus the constant that has been
    /// PROVEN to reproduce the old behaviour for each appended required parameter.
    internal sealed class RequiredForwardSpec
    {
        internal RequiredForwardSpec(string declaringType, string methodName,
                                     string[] oldParameterTypes, object[] appendedValues)
        {
            DeclaringType = declaringType;
            MethodName = methodName;
            OldParameterTypes = oldParameterTypes;
            AppendedValues = appendedValues;
        }

        internal string DeclaringType { get; private set; }
        internal string MethodName { get; private set; }
        internal string[] OldParameterTypes { get; private set; }

        /// One entry per appended parameter, in order. Only bool is supported: a wider
        /// type would need its own proof that the constant is unique, and none has one.
        internal object[] AppendedValues { get; private set; }

        internal string Describe()
        {
            return DeclaringType + "." + MethodName + "(" + string.Join(", ", OldParameterTypes) + ")";
        }
    }

    internal static class AppendedRequiredForwards
    {
        // EMPTY BY MEASUREMENT. The CookingStation.SpawnItem row above was written,
        // built and booted on Ulfsland 2026-09-13, and it made things WORSE: 13 error
        // lines became 21.
        //
        //   [Error  :  HarmonyX] Failed to patch void CookingStation::RPC_RemoveDoneItem
        //   AmbiguousMatchException: Ambiguous match found.
        //   Rethrow as TypeInitializationException: The type initializer for
        //     'PatchCookingStationItemMultiplier' threw an exception.
        //
        // The proven constant was not the problem - `false` does restate the old
        // behaviour, and that proof still stands below. The problem is the SECOND
        // OVERLOAD. CreatureLevelAndLootControl resolves SpawnItem BY NAME, so emitting a
        // 3-parameter sibling turned one match into two and killed the patch class that
        // was going to consume the forward. The mod we were trying to repair is the mod
        // the repair broke.
        //
        // This is the same failure AppendedOptionalForwards records for
        // Character.Message: by-name lookups do not survive an added overload unless
        // something detours AccessTools, which Wubarrk-Valheim10Compatibility does and
        // this patcher does not. Any row admitted here must therefore clear a SECOND bar
        // beyond a proven constant: no mod may resolve the method by name. Check with
        //   monodis --memberref <mod>.dll | grep -i AccessTools
        // and by booting, because only the boot counts.
        internal static readonly RequiredForwardSpec[] Table = { };

        internal static int Apply(AssemblyDefinition assembly, ManualLogSource log)
        {
            int emitted = 0;
            foreach (RequiredForwardSpec spec in Table)
            {
                if (Emit(assembly, spec, log))
                {
                    emitted++;
                }
            }
            return emitted;
        }

        private static bool Emit(AssemblyDefinition assembly, RequiredForwardSpec spec, ManualLogSource log)
        {
            TypeDefinition type = assembly.MainModule.GetType(spec.DeclaringType);
            if (type == null)
            {
                log.LogWarning("required-forward skipped, type not found: " + spec.Describe());
                return false;
            }

            MethodDefinition target = null;
            foreach (MethodDefinition candidate in type.Methods)
            {
                if (candidate.Name != spec.MethodName || candidate.IsStatic)
                {
                    continue;
                }
                if (candidate.Parameters.Count == spec.OldParameterTypes.Length &&
                    LeadingMatch(candidate, spec.OldParameterTypes))
                {
                    log.LogInfo("required-forward not needed, short overload already present: " + spec.Describe());
                    return false;
                }
                if (candidate.Parameters.Count != spec.OldParameterTypes.Length + spec.AppendedValues.Length ||
                    !LeadingMatch(candidate, spec.OldParameterTypes))
                {
                    continue;
                }
                if (target != null)
                {
                    log.LogWarning("required-forward REFUSED, ambiguous: " + spec.Describe());
                    return false;
                }
                target = candidate;
            }

            if (target == null)
            {
                log.LogWarning("required-forward skipped, no method whose leading parameters match: " + spec.Describe());
                return false;
            }

            // Each appended parameter must be a bool this table supplies. Anything else -
            // a different type, or an optional parameter that AppendedOptionalForwards
            // should have handled - is refused rather than guessed at.
            for (int i = 0; i < spec.AppendedValues.Length; i++)
            {
                ParameterDefinition appended = target.Parameters[spec.OldParameterTypes.Length + i];
                if (appended.ParameterType.FullName != "System.Boolean" || !(spec.AppendedValues[i] is bool))
                {
                    log.LogWarning(string.Format(
                        "required-forward REFUSED, appended parameter {0} is {1} and this table only proves bools: {2}",
                        appended.Name, appended.ParameterType.FullName, spec.Describe()));
                    return false;
                }
                if (appended.IsOptional)
                {
                    log.LogWarning("required-forward REFUSED, parameter is optional and belongs in the appended-optional table: " + spec.Describe());
                    return false;
                }
            }

            MethodDefinition forward = new MethodDefinition(
                target.Name,
                MethodAttributes.Public | MethodAttributes.HideBySig,
                target.ReturnType);
            foreach (string parameterType in spec.OldParameterTypes)
            {
                ParameterDefinition original = target.Parameters[forward.Parameters.Count];
                forward.Parameters.Add(new ParameterDefinition(original.Name, ParameterAttributes.None, original.ParameterType));
                if (original.ParameterType.FullName != parameterType)
                {
                    log.LogWarning("required-forward REFUSED, parameter type drift: " + spec.Describe());
                    return false;
                }
            }

            ILProcessor il = forward.Body.GetILProcessor();
            il.Emit(OpCodes.Ldarg_0);
            for (int i = 0; i < forward.Parameters.Count; i++)
            {
                il.Emit(OpCodes.Ldarg, forward.Parameters[i]);
            }
            foreach (object value in spec.AppendedValues)
            {
                il.Emit((bool)value ? OpCodes.Ldc_I4_1 : OpCodes.Ldc_I4_0);
            }
            il.Emit(OpCodes.Call, target);
            il.Emit(OpCodes.Ret);

            type.Methods.Add(forward);
            log.LogInfo(string.Format(
                "required-forward emitted: {0} -> {1}({2}) with proven constant{3} [{4}]",
                spec.Describe(), target.Name, target.Parameters.Count,
                spec.AppendedValues.Length == 1 ? "" : "s",
                string.Join(", ", Array.ConvertAll(spec.AppendedValues, v => v.ToString().ToLowerInvariant()))));
            return true;
        }

        private static bool LeadingMatch(MethodDefinition method, string[] parameterTypes)
        {
            if (method.Parameters.Count < parameterTypes.Length)
            {
                return false;
            }
            for (int i = 0; i < parameterTypes.Length; i++)
            {
                if (method.Parameters[i].ParameterType.FullName != parameterTypes[i])
                {
                    return false;
                }
            }
            return true;
        }
    }
}
