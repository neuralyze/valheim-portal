// Forwarding overloads for 1.0 methods whose RETURN TYPE changed to a
// narrower struct.
//
// ---------------------------------------------------------------------------
// The defect, 2026-09-12
// ---------------------------------------------------------------------------
// 1.0.12 changed ZDO.GetSector from returning Vector2i to returning Vector2s:
//
//   instance default valuetype [assembly_utils]Vector2s GetSector ()
//
// The runtime matches a callsite on its FULL signature, return type included,
// so a mod compiled against the Vector2i version fails to resolve:
//
//   MissingMethodException: Method not found: Vector2i .ZDO.GetSector()
//
// 6672 of those in a six-minute modded boot on Ulfsland, from
// MVP-Serverside_Simulations and Smoothbrain-CreatureLevelAndLootControl -
// and only visible at all once the appended-optional forwards let the world
// finish loading.
//
// ---------------------------------------------------------------------------
// Why two methods differing only in return type is safe here - MEASURED
// ---------------------------------------------------------------------------
// C# cannot express it, but IL can, and the question of whether the runtime
// then binds each callsite to the right one is not something to assume. It was
// measured on this Mono before any of this shipped, with a probe assembled by
// Cecil because the shape is not expressible in C#:
//
//   __ShimBindingProbe.Pick() as Vector2s -> body returning (1,2)
//   __ShimBindingProbe.Pick() as Vector2i -> body returning (30,40)
//
// The probe called both by full signature, boxed each result, and checked the
// runtime type name AND the value. Against the patched assembly it scored
// 10 (Vector2s callsite bound to the Vector2s body, x==1) + 5 (Vector2i
// callsite bound to the Vector2i body, x==30) + 100 (the real
// `Vector2i ZDO::GetSector()` callsite resolved) = exit 115. Against stock it
// died with the production error, `MissingMethodException: Method not found:
// Vector2i .ZDO.GetSector()`, before executing anything.
//
// So: the overloads coexist, and each callsite binds to the overload whose
// return type it names.
//
// ---------------------------------------------------------------------------
// The conversion rule, and what it refuses
// ---------------------------------------------------------------------------
// A forward is emitted only when the conversion is provably lossless:
//   - both the actual and the desired return type are structs
//   - the desired type has a constructor taking exactly as many parameters as
//     the actual type has public instance fields
//   - each field, in declaration order, widens to the corresponding
//     constructor parameter by a value-preserving conversion
// Vector2s{int16 x, int16 y} -> Vector2i(int32 _x, int32 _y) satisfies this:
// int16 -> int32 is widening, so no value can be misrepresented. (Note the
// constructor parameters are named _x/_y, not x/y, which is why the rule
// matches on position and type rather than on name.)
//
// Anything else is refused rather than approximated. A NARROWING conversion is
// the dangerous direction - Vector2i -> Vector2s would silently truncate a
// coordinate - and is never emitted.
//
// ---------------------------------------------------------------------------
// The cost, same as the appended-optional forwards
// ---------------------------------------------------------------------------
// ZDO.GetSector goes from one method to two, so any mod doing a name-only
// AccessTools.Method(typeof(ZDO), "GetSector") lookup will now get
// AmbiguousMatchException. That is the mechanism that regressed AzuAreaRepair
// when Character.Message gained an overload. Ambiguity counts are measured
// before and after every table change for exactly this reason.
//
// Measured this time, Ulfsland 2026-09-12, same image and mod set, two
// 6-minute boots differing only in this one table entry:
//   "MissingMethodException: Method not found: Vector2i .ZDO.GetSector()"
//       6813 -> 0
//   total error lines                        7074 -> 261
//   every ambiguity count                    UNCHANGED, line for line:
//       AmbiguousMatchException: Ambiguous match found.              8 -> 8
//       ... in Harmony patch for Character:Message                   3 -> 3
//       ... for HarmonyMethod[(class=Inventory, methodname=Load)]    2 -> 2
//       ... for HarmonyMethod[(class=EffectList, methodname=Create)] 2 -> 2
//       ... for HarmonyMethod[(class=PieceTable, ...SetCategory)]    1 -> 1
//   and no new error signature of any kind appeared.
// So nothing in this mod set looks ZDO.GetSector up by name alone, and this
// entry cost nothing. That is a measurement, not a guarantee for the next one.

using System;
using System.Collections.Generic;
using BepInEx.Logging;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Neuralyze.EverybodyShim
{
    /// One row: the signature a pre-1.0 mod emits, including the return type
    /// it expects, which is the whole point of this table.
    internal sealed class WidenedReturnSpec
    {
        internal WidenedReturnSpec(string declaringType, string methodName,
            string desiredReturnType, params string[] parameterTypes)
        {
            DeclaringType = declaringType;
            MethodName = methodName;
            DesiredReturnType = desiredReturnType;
            ParameterTypes = parameterTypes;
        }

        internal string DeclaringType { get; private set; }
        internal string MethodName { get; private set; }
        internal string DesiredReturnType { get; private set; }
        internal string[] ParameterTypes { get; private set; }

        internal string Describe()
        {
            return DesiredReturnType + " " + DeclaringType + "." + MethodName
                + "(" + string.Join(", ", ParameterTypes) + ")";
        }
    }

    internal static class ReturnTypeWidenedForwards
    {
        internal static readonly WidenedReturnSpec[] Table =
        {
            new WidenedReturnSpec("ZDO", "GetSector", "Vector2i"),

            // Same shape as GetSector and blocked by Valheim10Compatibility for
            // the same stated reason. 1.0.12 declares exactly one overload,
            //   default valuetype [assembly_utils]Vector2s GetZone(UnityEngine.Vector3 point)
            // and it is STATIC, which is the first time the emitter's static
            // path is used. 9 deployed mods reference it: CreatureLevelAndLoot-
            // Control, EpicLoot, More_World_Locations_AIO, Server_devcommands,
            // ServersideQoL, Serverside_Simulations, Upgrade_World, ValheimRcon,
            // World_Edit_Commands.
            new WidenedReturnSpec("ZoneSystem", "GetZone", "Vector2i",
                "UnityEngine.Vector3"),
        };

        /// Value-preserving conversions only. Keyed "from->to" on Cecil
        /// MetadataType names. Narrowing is absent by design, not by omission.
        private static readonly HashSet<string> Widening = new HashSet<string>
        {
            "SByte->Int16", "SByte->Int32", "SByte->Int64", "SByte->Single", "SByte->Double",
            "Byte->Int16", "Byte->UInt16", "Byte->Int32", "Byte->UInt32",
            "Byte->Int64", "Byte->UInt64", "Byte->Single", "Byte->Double",
            "Int16->Int32", "Int16->Int64", "Int16->Single", "Int16->Double",
            "UInt16->Int32", "UInt16->UInt32", "UInt16->Int64", "UInt16->UInt64",
            "UInt16->Single", "UInt16->Double",
            "Int32->Int64", "Int32->Double",
            "UInt32->Int64", "UInt32->UInt64", "UInt32->Double",
            "Single->Double",
        };

        internal static int Apply(AssemblyDefinition assembly, ManualLogSource log)
        {
            int emitted = 0;
            foreach (WidenedReturnSpec spec in Table)
            {
                if (Emit(assembly, spec, log))
                {
                    emitted++;
                }
            }
            return emitted;
        }

        private static bool Emit(AssemblyDefinition assembly, WidenedReturnSpec spec, ManualLogSource log)
        {
            TypeDefinition type = assembly.MainModule.GetType(spec.DeclaringType);
            if (type == null)
            {
                log.LogWarning("return-type forward skipped, type not found: " + spec.Describe());
                return false;
            }

            MethodDefinition actual = null;
            foreach (MethodDefinition candidate in type.Methods)
            {
                if (candidate.Name != spec.MethodName || !ParametersMatch(candidate, spec.ParameterTypes))
                {
                    continue;
                }
                if (candidate.ReturnType.FullName == spec.DesiredReturnType)
                {
                    log.LogInfo("return-type forward not needed, already present: " + spec.Describe());
                    return false;
                }
                if (actual != null)
                {
                    log.LogWarning("return-type forward REFUSED, ambiguous source: " + spec.Describe());
                    return false;
                }
                actual = candidate;
            }

            if (actual == null)
            {
                log.LogWarning("return-type forward skipped, no method with that name and parameters: " + spec.Describe());
                return false;
            }

            TypeDefinition from = actual.ReturnType.Resolve();
            TypeDefinition to = ResolveByName(assembly, actual, spec.DesiredReturnType);
            if (from == null || to == null)
            {
                log.LogWarning("return-type forward REFUSED, cannot resolve both struct types: " + spec.Describe());
                return false;
            }
            if (!from.IsValueType || !to.IsValueType)
            {
                log.LogWarning("return-type forward REFUSED, not both value types: " + spec.Describe());
                return false;
            }

            var fields = new List<FieldDefinition>();
            foreach (FieldDefinition f in from.Fields)
            {
                if (!f.IsStatic && f.IsPublic)
                {
                    fields.Add(f);
                }
            }

            MethodDefinition ctor = null;
            foreach (MethodDefinition candidate in to.Methods)
            {
                if (!candidate.IsConstructor || candidate.IsStatic ||
                    candidate.Parameters.Count != fields.Count)
                {
                    continue;
                }
                if (!WidensInto(fields, candidate))
                {
                    continue;
                }
                if (ctor != null)
                {
                    log.LogWarning("return-type forward REFUSED, more than one widening constructor: " + spec.Describe());
                    return false;
                }
                ctor = candidate;
            }

            if (ctor == null)
            {
                log.LogWarning(string.Format(
                    "return-type forward REFUSED, no lossless {0} -> {1} constructor: {2}",
                    from.FullName, to.FullName, spec.Describe()));
                return false;
            }

            ModuleDefinition module = assembly.MainModule;
            var forward = new MethodDefinition(actual.Name,
                MethodAttributes.Public | MethodAttributes.HideBySig
                    | (actual.IsStatic ? MethodAttributes.Static : 0),
                module.ImportReference(to));
            foreach (ParameterDefinition p in actual.Parameters)
            {
                forward.Parameters.Add(new ParameterDefinition(p.Name, ParameterAttributes.None, p.ParameterType));
            }

            var value = new VariableDefinition(actual.ReturnType);
            forward.Body.Variables.Add(value);
            forward.Body.InitLocals = true;
            ILProcessor il = forward.Body.GetILProcessor();

            if (!actual.IsStatic)
            {
                il.Append(il.Create(OpCodes.Ldarg_0));
            }
            for (int i = 0; i < forward.Parameters.Count; i++)
            {
                il.Append(il.Create(OpCodes.Ldarg, forward.Parameters[i]));
            }
            il.Append(il.Create(actual.IsVirtual ? OpCodes.Callvirt : OpCodes.Call, actual));
            il.Append(il.Create(OpCodes.Stloc, value));

            for (int i = 0; i < fields.Count; i++)
            {
                il.Append(il.Create(OpCodes.Ldloca, value));
                il.Append(il.Create(OpCodes.Ldfld, module.ImportReference(fields[i])));
                EmitWidening(il, fields[i].FieldType, ctor.Parameters[i].ParameterType);
            }
            il.Append(il.Create(OpCodes.Newobj, module.ImportReference(ctor)));
            il.Append(il.Create(OpCodes.Ret));

            type.Methods.Add(forward);
            log.LogInfo(string.Format(
                "return-type forward emitted: {0} (real method returns {1}; widened componentwise via {2}..ctor)",
                spec.Describe(), from.FullName, to.FullName));
            return true;
        }

        private static bool ParametersMatch(MethodDefinition method, string[] parameterTypes)
        {
            if (method.Parameters.Count != parameterTypes.Length)
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

        private static TypeDefinition ResolveByName(
            AssemblyDefinition assembly, MethodDefinition near, string fullName)
        {
            // The desired type lives in the same assembly as the actual return
            // type - Vector2i and Vector2s are both assembly_utils - so resolve
            // through that module rather than guessing at a reference.
            TypeDefinition sibling = near.ReturnType.Resolve();
            if (sibling != null)
            {
                TypeDefinition found = sibling.Module.GetType(fullName);
                if (found != null)
                {
                    return found;
                }
            }
            return assembly.MainModule.GetType(fullName);
        }

        private static bool WidensInto(List<FieldDefinition> fields, MethodDefinition ctor)
        {
            for (int i = 0; i < fields.Count; i++)
            {
                string from = fields[i].FieldType.MetadataType.ToString();
                string to = ctor.Parameters[i].ParameterType.MetadataType.ToString();
                if (from == to)
                {
                    continue;
                }
                if (!Widening.Contains(from + "->" + to))
                {
                    return false;
                }
            }
            return true;
        }

        /// Integral loads below 4 bytes already arrive on the stack as int32,
        /// so only the wider targets need an explicit conversion.
        private static void EmitWidening(ILProcessor il, TypeReference from, TypeReference to)
        {
            if (from.MetadataType == to.MetadataType)
            {
                return;
            }
            switch (to.MetadataType)
            {
                case MetadataType.Int64:
                    il.Append(il.Create(OpCodes.Conv_I8));
                    break;
                case MetadataType.UInt64:
                    il.Append(il.Create(OpCodes.Conv_U8));
                    break;
                case MetadataType.Single:
                    il.Append(il.Create(OpCodes.Conv_R4));
                    break;
                case MetadataType.Double:
                    il.Append(il.Create(OpCodes.Conv_R8));
                    break;
                default:
                    // Int16/UInt16/Int32/UInt32 targets: the stack value is
                    // already a correctly sign- or zero-extended int32.
                    break;
            }
        }
    }
}
