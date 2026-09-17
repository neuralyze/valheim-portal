// Defect 1. Classify the installed PieceManager UpdateAvailable patches by
// running them, remove only the ones that actually throw, and do the sizing
// ourselves on every pass.
//
// The rationale, the measurements and the deliberate non-choices (no catch, no
// unpatch-by-id, no field alias) are documented at the top of HammerFix.cs.
using S = global::System;
using SC = global::System.Collections.Generic;
using SR = global::System.Reflection;
using UE = global::UnityEngine;
using HL = global::HarmonyLib;

namespace Neuralyze.HammerFix
{
    internal static class AvailabilityPass
    {
        private const int CategoryCeiling = 4096;   // refuse, loudly, past this
        private const int AllCategory = 100;        // Piece.PieceCategory.All sentinel

        private static SR.MethodInfo _updateAvailable;
        private static SR.FieldInfo _byCategory;

        // Every patch method already classified, so the scratch invocation runs
        // once per method and never again.
        private static readonly SC.Dictionary<SR.MethodInfo, bool> _classified =
            new SC.Dictionary<SR.MethodInfo, bool>();
        private static int _lastPatchCount = -1;
        private static int _removed;
        // One shot. Reported after the first COMPLETED sweep, at Message level so it
        // reaches LogOutput.log: MEASURED in the operator's own installed config,
        // [Logging.Disk] LogLevels is "Fatal, Error, Warning, Message" and
        // [Logging.Console] Enabled is false, so an Info line exists nowhere the
        // operator can read it. Without this line, silence from the sweep cannot be
        // told apart from a sweep that never ran - and it genuinely does not run until
        // a local Player exists, because it is anchored on
        // Player.UpdateAvailablePiecesList. "Measured zero" and "no news" are
        // different answers.
        private static bool _reported;
        private static PieceTable _scratch;

        internal static SR.MethodInfo UpdateAvailable
        {
            get
            {
                if (_updateAvailable == null)
                    _updateAvailable = typeof(PieceTable).GetMethod("UpdateAvailable",
                        SR.BindingFlags.Instance | SR.BindingFlags.Public | SR.BindingFlags.NonPublic);
                return _updateAvailable;
            }
        }

        private static SC.List<SC.List<Piece>> ByCategory(PieceTable pt)
        {
            if (_byCategory == null)
                _byCategory = typeof(PieceTable).GetField("m_availablePiecesByCategory",
                    SR.BindingFlags.Instance | SR.BindingFlags.Public | SR.BindingFlags.NonPublic);
            return _byCategory == null ? null : (SC.List<SC.List<Piece>>)_byCategory.GetValue(pt);
        }

        internal static void Install(HL.Harmony h)
        {
            var target = UpdateAvailable;
            if (target == null)
            {
                HammerFixPlugin.Log.LogError("PieceTable.UpdateAvailable not found - pass repair NOT installed");
                return;
            }

            // Our own sizing, ahead of every other prefix.
            h.Patch(target,
                prefix: new HL.HarmonyMethod(typeof(AvailabilityPass).GetMethod(
                    nameof(SizePrefix), SR.BindingFlags.Static | SR.BindingFlags.NonPublic))
                { priority = HL.Priority.First });

            // The sweep anchor. Player.UpdateAvailablePiecesList is the sole
            // caller of UpdateAvailable in assembly_valheim (six call sites all
            // route through it), and it runs before the menu is read, so a
            // sweep here is always ahead of the pass it protects. A patch chain
            // cannot be edited from inside itself, which is why this is on the
            // CALLER and not on UpdateAvailable.
            var caller = typeof(Player).GetMethod("UpdateAvailablePiecesList",
                SR.BindingFlags.Instance | SR.BindingFlags.Public | SR.BindingFlags.NonPublic);
            if (caller == null)
                HammerFixPlugin.Log.LogError("Player.UpdateAvailablePiecesList not found - broken patches will NOT be swept");
            else
                h.Patch(caller,
                    prefix: new HL.HarmonyMethod(typeof(AvailabilityPass).GetMethod(
                        nameof(SweepPrefix), SR.BindingFlags.Static | SR.BindingFlags.NonPublic))
                    { priority = HL.Priority.First });
        }

        // ------------------------------------------------------------------
        // Sweep: remove the patches that the runtime says cannot run.
        // ------------------------------------------------------------------
        private static void SweepPrefix()
        {
            Sweep();
        }

        /// <summary>
        /// Classify and unpatch every unrunnable PieceManager UpdateAvailable
        /// patch currently installed. Idempotent; cheap after the first call.
        /// Public so a probe can drive it in a headless process, where nothing
        /// ever calls Player.UpdateAvailablePiecesList.
        /// </summary>
        public static void Sweep()
        {
            var target = UpdateAvailable;
            if (target == null) return;

            var info = HL.Harmony.GetPatchInfo(target);
            if (info == null) return;

            int count = (info.Prefixes == null ? 0 : info.Prefixes.Count)
                      + (info.Postfixes == null ? 0 : info.Postfixes.Count);
            if (count == _lastPatchCount) return;   // nothing new since last sweep
            _lastPatchCount = count;

            Classify(target, info.Prefixes);
            Classify(target, info.Postfixes);

            if (!_reported)
            {
                _reported = true;
                HammerFixPlugin.Log.LogMessage("swept " + count
                    + " UpdateAvailable patches (this plugin's own included), removed "
                    + _removed + " unrunnable");
            }
        }

        private static void Classify(SR.MethodInfo target, SC.IList<HL.Patch> patches)
        {
            if (patches == null) return;
            // Copy: unpatching mutates Harmony's own collection.
            var candidates = new SC.List<SR.MethodInfo>();
            for (int i = 0; i < patches.Count; i++)
            {
                var m = patches[i].PatchMethod;
                if (m == null) continue;
                if (m.DeclaringType == typeof(AvailabilityPass)) continue;
                if (_classified.ContainsKey(m)) continue;
                candidates.Add(m);
            }
            if (candidates.Count == 0) return;

            try { Judge(target, candidates); } finally { DropScratch(); }
        }

        private static void Judge(SR.MethodInfo target, SC.List<SR.MethodInfo> candidates)
        {
            for (int i = 0; i < candidates.Count; i++)
            {
                var m = candidates[i];
                S.Exception failure = Probe(m);
                bool unrunnable = IsMemberResolutionFailure(failure);
                _classified[m] = !unrunnable;

                string where = m.DeclaringType == null ? "?" : m.DeclaringType.FullName;
                string asm = "?";
                try { asm = m.DeclaringType.Assembly.GetName().Name; } catch { }

                if (!unrunnable)
                {
                    // A patch can fail on a bare scratch table for reasons that
                    // have nothing to do with this game version - no local
                    // Player, no Hud, an empty piece list. Those are ARGUMENT
                    // dependent and must never cost a healthy mod its patch.
                    if (failure != null)
                        HammerFixPlugin.Log.LogInfo("keeping " + where + "::" + m.Name + " from "
                            + asm + ": it threw " + failure.GetType().Name
                            + " on an empty scratch table, which is argument-dependent, not a "
                            + "missing member. Left installed.");
                    continue;
                }

                HammerFixPlugin.Log.LogWarning(
                    "REMOVING unrunnable patch " + where + "::" + m.Name + " from " + asm
                    + " - it throws " + failure.GetType().Name + ": " + failure.Message
                    + ". Its work (sizing m_availablePiecesByCategory and the selected-piece "
                    + "arrays) is done by HammerFix instead.");
                // Counted only where Unpatch actually returned: the summary must not
                // claim a removal the runtime refused.
                try { HammerFixPlugin.Harmony.Unpatch(target, m); _removed++; }
                catch (S.Exception e)
                {
                    HammerFixPlugin.Log.LogError("could NOT unpatch " + where + "::" + m.Name
                        + " - the availability pass will still abort. " + e);
                }
            }
        }

        /// <summary>
        /// True only for failures that mean the method cannot run on this
        /// assembly set AT ALL, whatever it is handed: a member the IL names
        /// does not exist. MEASURED here as
        ///   System.MissingFieldException: Field not found:
        ///   List`1&lt;List`1&lt;Piece&gt;&gt; PieceTable.m_availablePieces
        ///   Due to: Could not find field in class
        /// raised from inside the Harmony dynamic method for
        /// PieceTable::UpdateAvailable. Anything else is argument-dependent and
        /// is left alone.
        /// </summary>
        private static bool IsMemberResolutionFailure(S.Exception e)
        {
            for (; e != null; e = e.InnerException)
            {
                if (e is S.MissingMemberException) return true;   // field, method, member
                if (e is S.TypeLoadException) return true;
                if (e is S.FieldAccessException) return true;
                if (e is S.MethodAccessException) return true;
                if (e is S.BadImageFormatException) return true;
            }
            return false;
        }

        /// <summary>
        /// Ask the runtime whether a patch method can run at all, by running it
        /// against a PieceTable this plugin owns. Returns the exception, or null.
        /// </summary>
        private static S.Exception Probe(SR.MethodInfo m)
        {
            // Only single-PieceTable signatures are testable this way; anything
            // else is left strictly alone.
            var ps = m.GetParameters();
            if (ps.Length != 1 || ps[0].ParameterType != typeof(PieceTable)) return null;

            var scratch = Scratch();
            if (scratch == null) return null;
            try
            {
                m.Invoke(null, new object[] { scratch });
                return null;
            }
            catch (SR.TargetInvocationException e)
            {
                return e.InnerException ?? e;
            }
            catch (S.Exception e)
            {
                return e;
            }
            finally
            {
                // Keep the scratch table pristine for the next candidate.
                ResetScratch(scratch);
            }
        }

        /// <summary>
        /// A PieceTable that exists only for the duration of one classification
        /// sweep. It is DESTROYED afterwards rather than cached, because
        /// Resources.FindObjectsOfTypeAll&lt;PieceTable&gt;() is how the
        /// PieceManager copies and Jotunn find tables to register pieces into,
        /// and an empty extra one left lying around is a thing other code can
        /// trip over.
        /// </summary>
        private static PieceTable Scratch()
        {
            if (_scratch != null) return _scratch;
            try
            {
                var go = new UE.GameObject("HammerFixScratchPieceTable");
                go.SetActive(false);
                go.hideFlags = UE.HideFlags.HideAndDontSave;
                _scratch = go.AddComponent<PieceTable>();
                ResetScratch(_scratch);
                return _scratch;
            }
            catch (S.Exception e)
            {
                HammerFixPlugin.Log.LogError("could not create a scratch PieceTable, cannot "
                    + "classify patches: " + e);
                return null;
            }
        }

        private static void DropScratch()
        {
            if (_scratch == null) return;
            try { UE.Object.DestroyImmediate(_scratch.gameObject); }
            catch (S.Exception e) { HammerFixPlugin.Log.LogWarning("scratch PieceTable not destroyed: " + e.Message); }
            _scratch = null;
        }

        private static void ResetScratch(PieceTable pt)
        {
            // PieceTable::.ctor already allocates all of these; a patch that
            // grows them must find them non-null and small, exactly as it would
            // on a real table before the first pass.
            var by = ByCategory(pt);
            if (by == null)
            {
                if (_byCategory != null) _byCategory.SetValue(pt, new SC.List<SC.List<Piece>>());
            }
            else by.Clear();
            pt.m_selectedPiece = new UE.Vector2Int[VanillaMax];
            pt.m_lastSelectedPiece = new UE.Vector2Int[VanillaMax];
        }

        private static int VanillaMax
        {
            get
            {
                // (int)Piece.PieceCategory.Max, the size PieceTable::.ctor uses.
                try { return (int)S.Enum.Parse(typeof(Piece.PieceCategory), "Max"); }
                catch { return 9; }
            }
        }

        // ------------------------------------------------------------------
        // Sizing: correct, on every pass, independent of load order.
        // ------------------------------------------------------------------
        private static bool _ceilingReported;

        private static void SizePrefix(PieceTable __instance)
        {
            if (__instance == null) return;
            int need = Required(__instance);
            if (need <= 0) return;

            var by = ByCategory(__instance);
            if (by == null) return;
            while (by.Count < need) by.Add(new SC.List<Piece>());

            if (__instance.m_selectedPiece == null || __instance.m_selectedPiece.Length < need)
            {
                var a = __instance.m_selectedPiece;
                S.Array.Resize(ref a, need);
                __instance.m_selectedPiece = a;
            }
            if (__instance.m_lastSelectedPiece == null || __instance.m_lastSelectedPiece.Length < need)
            {
                var a = __instance.m_lastSelectedPiece;
                S.Array.Resize(ref a, need);
                __instance.m_lastSelectedPiece = a;
            }
        }

        /// <summary>
        /// How many category buckets this table actually needs. Three sources,
        /// because any one of them alone has been wrong here:
        ///   * the enum length, which is what vanilla's transpiled sizing loop
        ///     and ModifiedMaxCategory() use, and which Jotunn and the library
        ///     itself grow as mods register;
        ///   * the highest Piece.m_category actually present, which is what the
        ///     indexer in UpdateAvailable will reach - measured live above the
        ///     enum length on this fleet;
        ///   * the highest id the table DECLARES as a tab, which is what
        ///     GetSelectedCategory and the Hud tab loop will index.
        /// </summary>
        private static int Required(PieceTable pt)
        {
            int need = 0;
            try
            {
                int n = S.Enum.GetValues(typeof(Piece.PieceCategory)).Length - 1;
                if (n > need) need = n;
            }
            catch { }

            var pieces = pt.m_pieces;
            if (pieces != null)
                for (int i = 0; i < pieces.Count; i++)
                {
                    var go = pieces[i];
                    if (go == null) continue;
                    var p = go.GetComponent<Piece>();
                    if (p == null) continue;
                    int c = (int)p.m_category;
                    if (c == AllCategory) continue;
                    if (c + 1 > need) need = c + 1;
                }

            var cats = pt.m_categories;
            if (cats != null)
                for (int i = 0; i < cats.Count; i++)
                {
                    int c = (int)cats[i];
                    if (c == AllCategory) continue;
                    if (c + 1 > need) need = c + 1;
                }

            if (need > CategoryCeiling)
            {
                if (!_ceilingReported)
                {
                    _ceilingReported = true;
                    HammerFixPlugin.Log.LogError("REFUSING to size " + (pt.name ?? "?")
                        + " to " + need + " categories; clamping to " + CategoryCeiling
                        + ". Categories above " + CategoryCeiling
                        + " will still throw in PieceTable.UpdateAvailable. This is a clamp, not a fix.");
                }
                need = CategoryCeiling;
            }
            return need;
        }
    }
}
