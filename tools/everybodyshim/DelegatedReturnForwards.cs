// Forwarding overloads for 1.0 methods whose return SHAPE changed, where
// vanilla itself still provides the old shape under a different name.
//
// ---------------------------------------------------------------------------
// The defect, 2026-09-13
// ---------------------------------------------------------------------------
// 1.0.12 re-shaped ZDOMan.GetPortals. It used to hand back a flat list of
// portal ZDOs; it now returns per-sector buckets:
//
//   instance default class Dictionary`2<valuetype ZoneSystem/SectorIndex,
//                                       class List`1<class ZDO>> GetPortals()
//
// The runtime matches a callsite on its full signature, so a mod compiled
// against the old one fails to resolve:
//
//   MissingMethodException: Method not found:
//     System.Collections.Generic.List`1<ZDO> .ZDOMan.GetPortals()
//
// Observed firing, one line per boot, on the ulfsland-dn profile.
//
// ---------------------------------------------------------------------------
// Why this is a SEPARATE class from ReturnTypeWidenedForwards
// ---------------------------------------------------------------------------
// That table converts a struct into a wider struct componentwise, and refuses
// anything it cannot prove lossless. This is not that: Dictionary<K,V> ->
// List<T> is a change of SHAPE, with a dimension added, and no componentwise
// conversion exists. It was refused from the widening table on exactly those
// grounds.
//
// What makes it bridgeable at all is that vanilla 1.0 already computes the old
// shape itself, under a new name:
//
//   instance default class List`1<class ZDO> GetPortalList()
//
// whose body is precisely the flatten - newobj List<ZDO>, enumerate
// m_portalObjects, AddRange each bucket's value, return the list. So the
// bridge delegates rather than converts, and restores the old contract rather
// than approximating it. The emitter below performs NO conversion: it refuses
// unless the delegate target's return type is exactly the type the old
// callers expect.
//
// ---------------------------------------------------------------------------
// THE ASSUMPTION THIS RESTS ON - re-check it before adding any caller
// ---------------------------------------------------------------------------
// GetPortalList() is NOT the same object the old GetPortals() returned. The
// pre-1.0 method handed back the live backing collection; GetPortalList
// allocates a FRESH List<ZDO> on every call and copies into it. Two
// consequences:
//
//   1. It allocates per call. Fine for the call rates seen here, not fine in
//      a per-frame loop.
//   2. A caller that MUTATES the returned list - Add, Remove, Clear, or
//      writing through an index - used to change the game's portal set and
//      now silently changes a throwaway copy. That is silent data loss with
//      no exception, which is strictly worse than the MissingMethodException
//      this bridge removes.
//
// This bridge is safe today because of a fact about the profile, not a fact
// about the method: the SOLE referencing mod is
// Cross_Server_Portals/ValheimCrossServerPortals.dll, established by a
// monodis --memberref scan over every DLL in the deployed plugin tree, and
// both of its callsites only READ. One does GetEnumerator() straight off the
// result; the other stores it and runs Enumerable.Except against the mod's own
// knownPortals field. Neither mutates.
//
// So: IF ANY OTHER MOD EVER REFERENCES ZDOMan.GetPortals, THIS ASSUMPTION MUST
// BE RE-CHECKED before trusting the bridge. Re-run the memberref scan, read
// every new callsite, and confirm it does not mutate the returned list. A
// mutating caller must not get this bridge - it wants a real fix in the mod,
// and a crash is the better failure.
//
// ---------------------------------------------------------------------------
// Binding
// ---------------------------------------------------------------------------
// This adds a second GetPortals differing from vanilla's only by return type,
// the same arrangement already probe-proven twice for ZDO.GetSector (instance)
// and ZoneSystem.GetZone (static): distinct-bodied Pick() pairs called by full
// signature, checked on runtime type AND value, exit 115 patched against
// MissingMethodException on stock. GetPortals is an instance method, which is
// the case the GetSector probe covers exactly.

using System;
using System.Collections.Generic;
using BepInEx.Logging;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Neuralyze.EverybodyShim
{
    /// One row: the signature pre-1.0 mods emit, and the vanilla method that
    /// still returns that exact shape.
    internal sealed class DelegatedReturnSpec
    {
        internal DelegatedReturnSpec(string declaringType, string methodName,
            string desiredReturnType, string delegateTo, params string[] parameterTypes)
        {
            DeclaringType = declaringType;
            MethodName = methodName;
            DesiredReturnType = desiredReturnType;
            DelegateTo = delegateTo;
            ParameterTypes = parameterTypes;
        }

        internal string DeclaringType { get; private set; }
        internal string MethodName { get; private set; }
        internal string DesiredReturnType { get; private set; }
        internal string DelegateTo { get; private set; }
        internal string[] ParameterTypes { get; private set; }

        internal string Describe()
        {
            return DesiredReturnType + " " + DeclaringType + "." + MethodName
                + "(" + string.Join(", ", ParameterTypes) + ") -> " + DelegateTo;
        }
    }

    internal static class DelegatedReturnForwards
    {
        internal static readonly DelegatedReturnSpec[] Table =
        {
            // Safe only while Cross_Server_Portals remains the sole caller and
            // stays read-only - see the header. Fires once per boot unbridged.
            new DelegatedReturnSpec("ZDOMan", "GetPortals",
                "System.Collections.Generic.List`1<ZDO>", "GetPortalList"),
        };

        internal static int Apply(AssemblyDefinition assembly, ManualLogSource log)
        {
            int emitted = 0;
            foreach (DelegatedReturnSpec spec in Table)
            {
                if (Emit(assembly, spec, log))
                {
                    emitted++;
                }
            }
            return emitted;
        }

        private static bool Emit(AssemblyDefinition assembly, DelegatedReturnSpec spec, ManualLogSource log)
        {
            TypeDefinition type = assembly.MainModule.GetType(spec.DeclaringType);
            if (type == null)
            {
                log.LogWarning("delegated forward skipped, type not found: " + spec.Describe());
                return false;
            }

            MethodDefinition existing = null;
            MethodDefinition target = null;
            foreach (MethodDefinition candidate in type.Methods)
            {
                if (!ParametersMatch(candidate, spec.ParameterTypes))
                {
                    continue;
                }

                if (candidate.Name == spec.MethodName)
                {
                    if (candidate.ReturnType.FullName == spec.DesiredReturnType)
                    {
                        log.LogInfo("delegated forward not needed, already present: " + spec.Describe());
                        return false;
                    }
                    if (existing != null)
                    {
                        log.LogWarning("delegated forward REFUSED, ambiguous source: " + spec.Describe());
                        return false;
                    }
                    existing = candidate;
                }
                else if (candidate.Name == spec.DelegateTo)
                {
                    if (target != null)
                    {
                        log.LogWarning("delegated forward REFUSED, ambiguous delegate target: " + spec.Describe());
                        return false;
                    }
                    target = candidate;
                }
            }

            if (existing == null)
            {
                log.LogWarning("delegated forward skipped, no method of that name and parameters: " + spec.Describe());
                return false;
            }
            if (target == null)
            {
                log.LogWarning("delegated forward REFUSED, delegate target " + spec.DelegateTo
                    + " not found with matching parameters: " + spec.Describe());
                return false;
            }

            // No conversion is ever synthesized here. If vanilla's replacement
            // does not already return exactly what the old callers expect,
            // there is nothing safe to emit.
            if (target.ReturnType.FullName != spec.DesiredReturnType)
            {
                log.LogWarning(string.Format(
                    "delegated forward REFUSED, {0} returns {1}, not {2}: {3}",
                    spec.DelegateTo, target.ReturnType.FullName, spec.DesiredReturnType, spec.Describe()));
                return false;
            }
            if (target.IsStatic != existing.IsStatic)
            {
                log.LogWarning("delegated forward REFUSED, static mismatch between source and delegate target: "
                    + spec.Describe());
                return false;
            }

            var forward = new MethodDefinition(spec.MethodName,
                MethodAttributes.Public | MethodAttributes.HideBySig
                    | (target.IsStatic ? MethodAttributes.Static : 0),
                target.ReturnType);
            foreach (ParameterDefinition p in target.Parameters)
            {
                forward.Parameters.Add(new ParameterDefinition(p.Name, ParameterAttributes.None, p.ParameterType));
            }

            ILProcessor il = forward.Body.GetILProcessor();
            if (!target.IsStatic)
            {
                il.Append(il.Create(OpCodes.Ldarg_0));
            }
            for (int i = 0; i < forward.Parameters.Count; i++)
            {
                il.Append(il.Create(OpCodes.Ldarg, forward.Parameters[i]));
            }
            il.Append(il.Create(target.IsVirtual ? OpCodes.Callvirt : OpCodes.Call, target));
            il.Append(il.Create(OpCodes.Ret));

            type.Methods.Add(forward);
            log.LogInfo(string.Format(
                "delegated forward emitted: {0} (vanilla {1}.{2} now returns {3}; delegating to {4}, "
                + "which allocates a fresh list per call)",
                spec.Describe(), spec.DeclaringType, spec.MethodName,
                existing.ReturnType.FullName, spec.DelegateTo));
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
    }
}
