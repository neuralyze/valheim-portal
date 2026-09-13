// Forwarding overloads for 1.0 methods that gained an appended OPTIONAL
// parameter.
//
// ---------------------------------------------------------------------------
// The defect class, 2026-09-12
// ---------------------------------------------------------------------------
// Valheim 1.0 appended optional parameters to methods that mods call. C#
// resolves optional arguments at COMPILE time, so a mod compiled against the
// shorter method emits a callsite naming the shorter signature exactly:
//
//   callvirt instance void Character::Message(MessageHud/MessageType, string,
//                                             int32, UnityEngine.Sprite)
//
// The runtime matches callsites by exact signature, and no such method exists
// any more, so every call throws. Measured on the modded Ulfsland boot of
// 2026-09-12: 8756 of
//   MissingMethodException: Method not found:
//     void .Character.Message(MessageHud/MessageType,string,int,UnityEngine.Sprite)
// and 8756 of
//   MissingMethodException: Method not found:
//     StatusEffect .SEMan.AddStatusEffect(StatusEffect,bool,int,single)
// in seven minutes, repeating every frame from "Activating first scene!"
// onward. Harmony fails the same way for a different reason:
// AccessTools.DeclaredMethod matches on exact arity, so a patch declaring the
// old parameter list resolves to null and PatchProcessor.Patch throws
// "Undefined target method for patch method ...".
//
// Synthesizing the old overload as a forwarder fixes both, and is safe for the
// same reason the const conversion was: shipped 1.0 IL calls the LONG form
// explicitly, so an added short overload is unreachable from anything that
// already exists and cannot change vanilla behaviour.
//
// ---------------------------------------------------------------------------
// What this does NOT fix - read before believing a mod works
// ---------------------------------------------------------------------------
// A Harmony patch whose declared target is the old signature will now RESOLVE,
// and will be applied to the forwarder. Vanilla code does not call the
// forwarder, so such a patch never runs. The mod loads instead of throwing,
// and that specific patched behaviour is silently inert. That is strictly
// better than a per-frame exception loop, but it is not "the mod works", and
// anything depending on such a patch must be verified individually. Direct
// CALLS from mod code are genuinely fixed; Harmony PATCHES of the old
// signature are only de-fanged.
//
// It also has a REAL COST, paid by mods that look a method up by name alone.
// AccessTools.Method(typeof(Character), "Message") with no argument list
// throws AmbiguousMatchException once a type has two same-named methods, and
// Character.Message and EffectList.Create each went from one overload to two.
// Measured on Ulfsland 2026-09-12, before -> after emitting the forwards:
//   "Ambiguous match in Harmony patch for Character:Message"   0 -> 3
//   "Ambiguous match for HarmonyMethod[(class=EffectList, ...)]" 0 -> 2
// AzuAreaRepair.PlayerRepairTranspiler regressed from loading to failing
// because of exactly this. The trade was still overwhelmingly positive -
// 17668 error lines down to 260 in the same window - but it is a trade, not a
// free win, and a mod that stops loading after a new table entry is added
// should be suspected of a name-only lookup before anything else.
//
// ---------------------------------------------------------------------------
// Why the table carries no default values
// ---------------------------------------------------------------------------
// An entry names only the declaring type, the method name, and the old
// parameter list. The appended defaults are read out of the target assembly's
// own metadata at patch time, so a hand-typed default cannot drift from what
// the game actually declares. An entry is REFUSED unless exactly one method
// matches the old parameter list as a prefix and every remaining parameter is
// optional with a resolvable default. Refusing is the correct outcome for a
// parameter that became REQUIRED - forwarding one of those would mean
// inventing a value, which for an inventory or placement call means silently
// corrupting state rather than crashing.
//
// ---------------------------------------------------------------------------
// Entries, each read from the 1.0.12 IL declaration with its .param defaults
// ---------------------------------------------------------------------------
//   Character.Message
//     instance void Message(MessageHud/MessageType, string, [opt] int32,
//                           [opt] UnityEngine.Sprite, [opt] bool log)
//     .param [3] = int32(0)  .param [4] = nullref  .param [5] = bool(false)
//     old callers pass 4. log=false reproduces the old method, which had no
//     log parameter and logged nothing.
//
//   SEMan.AddStatusEffect  (both overloads)
//     instance StatusEffect AddStatusEffect(int32 nameHash, [opt] bool,
//                                           [opt] int32, [opt] float32,
//                                           [opt] int16 variant)
//     instance StatusEffect AddStatusEffect(StatusEffect, [opt] bool,
//                                           [opt] int32, [opt] float32,
//                                           [opt] int16 variant)
//     .param [5] = int16(0xffff) = -1 on both. old callers pass 4, and -1 is
//     the "no variant specified" value the old method had no way to express.
//
//   Inventory.AddItem
//     instance bool AddItem(ItemDrop/ItemData, int32, int32, int32,
//                           [opt] bool skipValidPositionCheck)
//     .param [5] = bool(false). false means "do perform the valid-position
//     check", which is what the old four-argument method did.
//
//   EffectList.Create
//     instance UnityEngine.GameObject[] Create(Vector3, Quaternion,
//       [opt] Transform, [opt] float32, [opt] int32, [opt] ZDOID
//       gamepadEffectsExclusiveToPlayer)
//     .param [3] = nullref  [4] = float32(1.)  [5] = int32(0xffffffff)
//     [6] = nullref. Parameter 6 is a STRUCT, so its nullref constant means
//     default(ZDOID); the emitter zero-initializes a local rather than
//     pushing ldnull, which would not verify.
//
// Every claim above is `grep`-able out of the disassembly:
//   cd <Managed> && MONO_PATH=. monodis --output=av.il assembly_valheim.dll
//
// NOT admitted, deliberately, because the added parameter is REQUIRED and has
// no default - forwarding would invent a value: Humanoid.IsTeleportable,
// Inventory.IsTeleportable, Piece.SetCreator, VisEquipment.SetLeftItem /
// SetRightItem / SetLeftBackItem / SetRightBackItem / SetShoulderItem,
// CookingStation.SpawnItem. Also not admitted: ZDOMan.FindSectorObjects, where
// the arity was REDUCED rather than appended to.
//
// Also NOT admitted: Terminal/ConsoleCommand..ctor, which looks class-B-shaped
// in the log but is not. The mod wants 12 parameters:
//   (string, string, Terminal/ConsoleEvent, bool, bool, bool, bool, bool,
//    Terminal/ConsoleOptionsFetcher, bool, bool, bool)
// 1.0.12 declares 13, and the new one - `[opt] bool hideBehindDevCommands`,
// .param [9] = bool(false) - was INSERTED at position 9, ahead of
// optionsFetcher, not appended. The old list is therefore not a prefix of the
// new one: old parameter 9 is ConsoleOptionsFetcher where new parameter 9 is a
// bool. This emitter's prefix rule refuses it automatically, which is the
// right outcome - a mid-list insertion is a THIRD defect shape needing its own
// proof that the positional remapping is unique, and it has not been done.
// (It is also a constructor, which this emitter does not synthesize at all.)

using System;
using System.Collections.Generic;
using System.Text;
using BepInEx.Logging;
using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Neuralyze.EverybodyShim
{
    /// One row of the forward table: the short signature a pre-1.0 mod emits.
    internal sealed class ForwardSpec
    {
        internal ForwardSpec(string declaringType, string methodName, params string[] oldParameterTypes)
        {
            DeclaringType = declaringType;
            MethodName = methodName;
            OldParameterTypes = oldParameterTypes;
        }

        internal string DeclaringType { get; private set; }
        internal string MethodName { get; private set; }

        /// Cecil TypeReference.FullName of each parameter of the OLD signature,
        /// in order. Matched exactly, which is what disambiguates the two
        /// SEMan.AddStatusEffect overloads and the nine Inventory.AddItem ones.
        internal string[] OldParameterTypes { get; private set; }

        internal string Describe()
        {
            return DeclaringType + "." + MethodName + "(" + string.Join(", ", OldParameterTypes) + ")";
        }
    }

    internal static class AppendedOptionalForwards
    {
        // ONE ROW, added 2026-09-13 for exactly the reason the note below predicted.
        //
        // PlayerProfile.IncrementStat is the appended-optional shape in its purest form:
        //   1.0.12:  instance void IncrementStat(PlayerStatType stat,
        //                                        [opt] float32 amount, [opt] bool cheated)
        //   arities: total=[3] required=[1]
        // Both appended parameters carry the GAME'S OWN .param defaults, so the forward
        // restates Valheim's declared behaviour rather than inventing anything - the
        // distinction that separates this table from AppendedRequiredForwards.
        //
        // Why it matters: a player's own log showed 898 occurrences of
        //   MissingMethodException: Method not found: void .PlayerProfile.IncrementStat(PlayerStatType,single)
        // across 39 SECONDS in-world. Stat increments run on a hot path, so every step
        // and every swing threw. EpicLoot and LongshipUpgrades call the two-parameter
        // form; Smoothbrain-Farming did too and has since been dropped.
        //
        // Wubarrk-Valheim10Compatibility does NOT bridge it: its client log names only
        // its two BLOCKED return-type refusals, its own literals cover
        // Game.SavePlayerProfile and PlayerProfile.GetCharacterFolderPath, and the 898
        // runtime exceptions settle the question whatever its table says.
        //
        // The second bar - the one the SpawnItem reversal added - is met: NO installed mod
        // resolves this method by name. Swept all 115 deployed DLLs for a UTF-16
        // "IncrementStat" literal and found zero, the ASCII hits being metadata names
        // rather than reflection strings. So the extra overload cannot break a by-name
        // lookup the way our Character.Message forward once did.
        //
        // The rest of this table stays EMPTY BY DECISION, not by oversight.
        // Character.Message, SEMan.AddStatusEffect (both overloads), Inventory.AddItem
        // and EffectList.Create all lived here until 2026-09-13 and are all now
        // bridged by Wubarrk-Valheim10Compatibility, which additionally
        // detours AccessTools so by-name patches survive the extra overload.
        // Ours did not, and measured worse for it: with our Character.Message
        // forward beside theirs, AzuAreaRepair.PlayerRepairTranspiler died at
        // PatchAll on an ambiguous by-name lookup that their hook had just
        // fixed (75 error lines), against 41 with ours cut back.
        //
        // The emitter below is kept deliberately. The appended-optional shape
        // is how Valheim breaks mods at every content patch, and when the next
        // one lands this is a table row rather than new code - with the prefix
        // rule and the refusals already proven.
        internal static readonly ForwardSpec[] Table =
        {
            new ForwardSpec("PlayerProfile", "IncrementStat",
                new[] { "PlayerStatType", "System.Single" }),
        };

        /// Emits every admissible forward. Returns the number emitted.
        internal static int Apply(AssemblyDefinition assembly, ManualLogSource log)
        {
            int emitted = 0;
            foreach (ForwardSpec spec in Table)
            {
                if (Emit(assembly, spec, log))
                {
                    emitted++;
                }
            }
            return emitted;
        }

        private static bool Emit(AssemblyDefinition assembly, ForwardSpec spec, ManualLogSource log)
        {
            TypeDefinition type = assembly.MainModule.GetType(spec.DeclaringType);
            if (type == null)
            {
                log.LogWarning("forward skipped, type not found: " + spec.Describe());
                return false;
            }

            MethodDefinition target = null;
            foreach (MethodDefinition candidate in type.Methods)
            {
                if (candidate.Name != spec.MethodName)
                {
                    continue;
                }

                if (MatchesExactly(candidate, spec.OldParameterTypes))
                {
                    // The short overload already exists - either a game build
                    // that never appended, or a second pass over an assembly
                    // this patcher already processed.
                    log.LogInfo("forward not needed, short overload already present: " + spec.Describe());
                    return false;
                }

                if (!IsAppendedOptionalForm(candidate, spec.OldParameterTypes))
                {
                    continue;
                }

                if (target != null)
                {
                    log.LogWarning("forward REFUSED, ambiguous - more than one candidate matches: " + spec.Describe());
                    return false;
                }

                target = candidate;
            }

            if (target == null)
            {
                log.LogWarning("forward skipped, no method whose leading parameters match: " + spec.Describe());
                return false;
            }

            MethodDefinition forward = BuildForward(assembly, type, target, spec.OldParameterTypes.Length, log);
            if (forward == null)
            {
                return false;
            }

            type.Methods.Add(forward);
            log.LogInfo(string.Format(
                "forward emitted: {0} -> {1} arg{2} appended [{3}]",
                spec.Describe(),
                target.Parameters.Count - spec.OldParameterTypes.Length,
                target.Parameters.Count - spec.OldParameterTypes.Length == 1 ? "" : "s",
                DescribeAppended(target, spec.OldParameterTypes.Length)));
            return true;
        }

        private static bool MatchesExactly(MethodDefinition method, string[] parameterTypes)
        {
            if (method.Parameters.Count != parameterTypes.Length)
            {
                return false;
            }
            return LeadingParametersMatch(method, parameterTypes);
        }

        /// True when `method` is the old signature plus one or more trailing
        /// parameters that are ALL optional. A trailing REQUIRED parameter
        /// disqualifies the method: forwarding onto it would mean inventing a
        /// value, which this patcher never does.
        private static bool IsAppendedOptionalForm(MethodDefinition method, string[] oldParameterTypes)
        {
            if (method.Parameters.Count <= oldParameterTypes.Length)
            {
                return false;
            }
            if (!LeadingParametersMatch(method, oldParameterTypes))
            {
                return false;
            }
            for (int i = oldParameterTypes.Length; i < method.Parameters.Count; i++)
            {
                if (!method.Parameters[i].IsOptional)
                {
                    return false;
                }
            }
            return true;
        }

        private static bool LeadingParametersMatch(MethodDefinition method, string[] parameterTypes)
        {
            for (int i = 0; i < parameterTypes.Length; i++)
            {
                if (method.Parameters[i].ParameterType.FullName != parameterTypes[i])
                {
                    return false;
                }
            }
            return true;
        }

        private static MethodDefinition BuildForward(
            AssemblyDefinition assembly, TypeDefinition type, MethodDefinition target, int keep, ManualLogSource log)
        {
            MethodAttributes attributes = MethodAttributes.Public | MethodAttributes.HideBySig;
            if (target.IsStatic)
            {
                attributes |= MethodAttributes.Static;
            }

            var forward = new MethodDefinition(target.Name, attributes, target.ReturnType);
            for (int i = 0; i < keep; i++)
            {
                ParameterDefinition source = target.Parameters[i];
                forward.Parameters.Add(new ParameterDefinition(source.Name, ParameterAttributes.None, source.ParameterType));
            }

            ILProcessor il = forward.Body.GetILProcessor();

            if (!target.IsStatic)
            {
                il.Append(il.Create(OpCodes.Ldarg_0));
            }
            for (int i = 0; i < keep; i++)
            {
                il.Append(il.Create(OpCodes.Ldarg, forward.Parameters[i]));
            }

            for (int i = keep; i < target.Parameters.Count; i++)
            {
                if (!EmitDefault(assembly, forward, il, target.Parameters[i], log))
                {
                    return null;
                }
            }

            // `call` rather than `callvirt`: the forwarder and its target are
            // the same type, and a virtual target still dispatches correctly
            // through callvirt only when the instance may be a subclass, which
            // is why virtual targets keep callvirt below.
            il.Append(il.Create(target.IsVirtual ? OpCodes.Callvirt : OpCodes.Call, target));
            il.Append(il.Create(OpCodes.Ret));

            return forward;
        }

        /// Pushes the parameter's declared default. Never guesses: an
        /// unrepresentable default aborts the whole forward.
        private static bool EmitDefault(
            AssemblyDefinition assembly, MethodDefinition forward, ILProcessor il,
            ParameterDefinition parameter, ManualLogSource log)
        {
            TypeReference parameterType = parameter.ParameterType;
            object value = parameter.HasConstant ? parameter.Constant : null;

            if (value == null)
            {
                // A nullref constant on a reference type is literally null. On
                // a STRUCT - EffectList.Create's ZDOID - it means default(T),
                // which has to be a zero-initialized local; ldnull would not
                // verify.
                if (parameterType.IsValueType)
                {
                    var local = new VariableDefinition(parameterType);
                    forward.Body.Variables.Add(local);
                    forward.Body.InitLocals = true;
                    il.Append(il.Create(OpCodes.Ldloca_S, local));
                    il.Append(il.Create(OpCodes.Initobj, parameterType));
                    il.Append(il.Create(OpCodes.Ldloc, local));
                    return true;
                }
                il.Append(il.Create(OpCodes.Ldnull));
                return true;
            }

            if (value is bool)
            {
                il.Append(il.Create((bool)value ? OpCodes.Ldc_I4_1 : OpCodes.Ldc_I4_0));
                return true;
            }
            if (value is sbyte || value is byte || value is short || value is ushort ||
                value is int || value is uint || value is char)
            {
                il.Append(il.Create(OpCodes.Ldc_I4, Convert.ToInt32(value)));
                return true;
            }
            if (value is long || value is ulong)
            {
                il.Append(il.Create(OpCodes.Ldc_I8, Convert.ToInt64(value)));
                return true;
            }
            if (value is float)
            {
                il.Append(il.Create(OpCodes.Ldc_R4, (float)value));
                return true;
            }
            if (value is double)
            {
                il.Append(il.Create(OpCodes.Ldc_R8, (double)value));
                return true;
            }
            if (value is string)
            {
                il.Append(il.Create(OpCodes.Ldstr, (string)value));
                return true;
            }

            log.LogWarning(string.Format(
                "forward REFUSED, cannot represent default for parameter '{0}' of type {1} (constant {2})",
                parameter.Name, parameterType.FullName, value.GetType().FullName));
            return false;
        }

        private static string DescribeAppended(MethodDefinition target, int keep)
        {
            var text = new StringBuilder();
            for (int i = keep; i < target.Parameters.Count; i++)
            {
                if (text.Length > 0)
                {
                    text.Append(", ");
                }
                ParameterDefinition parameter = target.Parameters[i];
                object value = parameter.HasConstant ? parameter.Constant : null;
                text.Append(parameter.Name).Append('=').Append(value == null ? "default" : value.ToString());
            }
            return text.ToString();
        }
    }
}
