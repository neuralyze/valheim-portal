// Defect 2. Raise the 15x6 icon grid, from ONE pair of providers, at all eight
// inlined sites, and keep the panel footprint the size it already is.
//
// WHY THE FOOTPRINT AND NOT THE SCREEN. MEASURED, Hud::UpdatePieceList
// IL_0092..IL_00b8: each icon is placed at
//   anchoredPosition = (col * m_pieceIconSpacing, -row * m_pieceIconSpacing)
// inside Hud.m_pieceListRoot, and nothing in assembly_valheim resizes that
// root, the window, or Hud.m_pieceListMask - a serialized RectTransform with
// ZERO code references, i.e. a purely prefab-side clip. So a wider grid runs
// right and a taller grid runs down, into or past that clip. Adding columns
// alone does not add reachable cells; it adds cells nobody can see.
//
// What DOES add reachable cells is shrinking the cell, and the clip gives the
// exact budget. MEASURED in a loaded process with the full plugin set:
//   Hud.m_pieceIconSpacing            70      (the shipped prefab value, NOT
//                                              the 64 in Hud::.ctor)
//   Hud.m_pieceListMask.rect          1050 x 416
//   Hud.m_pieceSelectionWindow.rect   1085 x 490
//   Hud.m_pieceListRoot.rect          0 x 0, anchorMin = anchorMax = (0,1)
// 1050 / 70 is exactly 15, and 416 / 70 is 5.94 - the clip IS the vanilla
// 15x6 block, to the unit. And the clip is the icons' own container, not a
// sibling: the ancestor chains measure as
//   listRoot: Root < PieceList < SelectionWindow < bar < BuildHud < ...
//   mask:            PieceList < SelectionWindow < bar < BuildHud < ...
// so the mask object IS `PieceList`, two levels above every icon. Scaling
// m_pieceListRoot therefore shrinks cell pitch and icon size together, inside
// that clip, and the on-screen footprint does not move at all. The datum is
// the clip rect, taken from the thing that does the clipping - not the 0x0
// rect of the positioning origin, which would have been a measurement of
// nothing.
//
// The grid is then the SMALLEST grid in that clip which holds the fullest
// category, so icons stay as large as the content allows, floored at
// MinIconPixels. What it cannot hold is logged with the number of pieces it
// cannot reach.
//
// The eight sites, all MEASURED in assembly_valheim.dll of the live install,
// with the literal count that must be found at each:
//   Hud::UpdatePieceList        15 x1 -> width   ;  6 x1 -> height
//   Hud::GetSelectedGrid        15 x1 -> width   ;  6 x1 -> height
//   PieceTable::GetPiece        15 x1 -> width          (p.y*w + p.x)
//   PieceTable::GetPieceIndex   15 x3 -> width          (i%w, (i-i%w)/w)
//   PieceTable::RightPiece      15 x1 -> width          (wrap x at w)
//   PieceTable::LeftPiece       14 x1 -> width - 1      (wrap x to w-1)
//   PieceTable::DownPiece        6 x1 -> height         (wrap y at h)
//   PieceTable::UpPiece          5 x1 -> height - 1     (wrap y to h-1)
// Every one is rewritten from Width()/Height(), so the row*width+col that
// UpdatePieceList builds and the row*width+col that GetPiece resolves cannot
// diverge. Each target gets exactly ONE transpiler holding its own spec, so a
// later patch on the same method cannot make an earlier transpiler run against
// the wrong spec. A method whose literal count does not match is left
// UNPATCHED and reported; a half-rewritten index map is worse than none.
//
// Nothing else indexes m_pieceIcons. MEASURED: its only readers in
// assembly_valheim are UpdatePieceList, GetSelectedGrid, UpdatePieceBuildStatus
// and UpdatePieceBuildStatusAll, and the last two are driven entirely by
// m_pieceIcons.Count. Cecil scan of all 119 plugin DLLs on the live set: zero
// field references to m_pieceIcons. One honest cost: UpdatePieceBuildStatus
// recolours ONE icon per frame, so a full affordability sweep of the grid takes
// as many frames as there are cells - 90 becomes 308 at the measured defaults.
using System.Collections.Generic;
using HarmonyLib;
using S = global::System;
using SC = global::System.Collections.Generic;
using SR = global::System.Reflection;
using SRE = global::System.Reflection.Emit;
using UE = global::UnityEngine;

namespace Neuralyze.HammerFix
{
    internal static class BuildGrid
    {
        private const int VanillaWidth = 15;
        private const int VanillaHeight = 6;

        private static int _width = VanillaWidth;
        private static int _height = VanillaHeight;
        private static float _scale = 1f;

        /// <summary>Largest available-piece count in any one category, taken from
        /// the availability pass rather than from the registered counts, because
        /// a piece the player does not know consumes no cell.</summary>
        private static int _fullestCategory;
        private static int _reportedShortfall = -1;
        private static string _reportedGrid;

        private static int Width() { return _width; }
        private static int Height() { return _height; }
        private static int WidthMinusOne() { return _width - 1; }
        private static int HeightMinusOne() { return _height - 1; }

        // ------------------------------------------------------------------
        // Install
        // ------------------------------------------------------------------
        internal static void Install(Harmony h)
        {
            Patch(h, typeof(Hud), "UpdatePieceList", null, nameof(UpdatePieceList_Transpiler));
            Patch(h, typeof(Hud), "GetSelectedGrid", null, nameof(GetSelectedGrid_Transpiler));
            Patch(h, typeof(PieceTable), "GetPiece",
                new S.Type[] { typeof(Piece.PieceCategory), typeof(UE.Vector2Int) },
                nameof(GetPiece_Transpiler));
            Patch(h, typeof(PieceTable), "GetPieceIndex", null, nameof(GetPieceIndex_Transpiler));
            Patch(h, typeof(PieceTable), "RightPiece", null, nameof(RightPiece_Transpiler));
            Patch(h, typeof(PieceTable), "LeftPiece", null, nameof(LeftPiece_Transpiler));
            Patch(h, typeof(PieceTable), "DownPiece", null, nameof(DownPiece_Transpiler));
            Patch(h, typeof(PieceTable), "UpPiece", null, nameof(UpPiece_Transpiler));

            // Keep the footprint: the icons are children of m_pieceListRoot, so
            // scaling it scales cell pitch and icon size together.
            var upl = Find(typeof(Hud), "UpdatePieceList", null);
            if (upl != null)
                h.Patch(upl, postfix: new HarmonyMethod(Self(nameof(ScalePostfix))));

            // Learn the fullest category from the pass that computes it.
            var ua = AvailabilityPass.UpdateAvailable;
            if (ua != null)
                h.Patch(ua, postfix: new HarmonyMethod(Self(nameof(MeasurePostfix)))
                { priority = Priority.Last });
        }

        private static SR.MethodInfo Self(string name)
        {
            return typeof(BuildGrid).GetMethod(name,
                SR.BindingFlags.Static | SR.BindingFlags.NonPublic);
        }

        private static SR.MethodInfo Find(S.Type t, string name, S.Type[] args)
        {
            var flags = SR.BindingFlags.Instance | SR.BindingFlags.Public | SR.BindingFlags.NonPublic;
            return args == null ? t.GetMethod(name, flags)
                                : t.GetMethod(name, flags, null, args, null);
        }

        private static void Patch(Harmony h, S.Type t, string name, S.Type[] args, string transpiler)
        {
            var target = Find(t, name, args);
            if (target == null)
            {
                HammerFixPlugin.Log.LogError(t.Name + "." + name
                    + " not found - that site keeps the vanilla grid literal");
                return;
            }
            try { h.Patch(target, transpiler: new HarmonyMethod(Self(transpiler))); }
            catch (S.Exception e)
            {
                HammerFixPlugin.Log.LogError("could not transpile " + t.Name + "." + name
                    + ": " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // One transpiler per target, each holding its own measured spec.
        // ------------------------------------------------------------------
        private static SC.IEnumerable<CodeInstruction> UpdatePieceList_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("Hud.UpdatePieceList", src,
                new Spec(15, 1, nameof(Width)), new Spec(6, 1, nameof(Height)));
        }

        private static SC.IEnumerable<CodeInstruction> GetSelectedGrid_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("Hud.GetSelectedGrid", src,
                new Spec(15, 1, nameof(Width)), new Spec(6, 1, nameof(Height)));
        }

        private static SC.IEnumerable<CodeInstruction> GetPiece_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("PieceTable.GetPiece", src, new Spec(15, 1, nameof(Width)));
        }

        private static SC.IEnumerable<CodeInstruction> GetPieceIndex_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("PieceTable.GetPieceIndex", src, new Spec(15, 3, nameof(Width)));
        }

        private static SC.IEnumerable<CodeInstruction> RightPiece_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("PieceTable.RightPiece", src, new Spec(15, 1, nameof(Width)));
        }

        private static SC.IEnumerable<CodeInstruction> LeftPiece_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("PieceTable.LeftPiece", src, new Spec(14, 1, nameof(WidthMinusOne)));
        }

        private static SC.IEnumerable<CodeInstruction> DownPiece_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("PieceTable.DownPiece", src, new Spec(6, 1, nameof(Height)));
        }

        private static SC.IEnumerable<CodeInstruction> UpPiece_Transpiler(
            SC.IEnumerable<CodeInstruction> src)
        {
            return Rewrite("PieceTable.UpPiece", src, new Spec(5, 1, nameof(HeightMinusOne)));
        }

        private struct Spec
        {
            public readonly int Value;
            public readonly int Expected;
            public readonly string Provider;
            public Spec(int value, int expected, string provider)
            { Value = value; Expected = expected; Provider = provider; }
        }

        // Every rewrite outcome, so the result can be READ rather than
        // inferred from whether an error line appeared in a log whose level
        // filtering is not ours to control.
        private static readonly SC.List<string> _report = new SC.List<string>();

        /// <summary>One line per site: what was rewritten, or why it was not.</summary>
        public static string RewriteReport()
        {
            return string.Join(" ;; ", _report.ToArray());
        }

        private static SC.List<CodeInstruction> Rewrite(string where,
            SC.IEnumerable<CodeInstruction> src, params Spec[] specs)
        {
            var list = new SC.List<CodeInstruction>(src);

            for (int s = 0; s < specs.Length; s++)
            {
                int hits = 0;
                for (int i = 0; i < list.Count; i++)
                    if (list[i].LoadsConstant(specs[s].Value)) hits++;
                if (hits == specs[s].Expected) continue;
                string refusal = where + ":" + specs[s].Value + " REFUSED found=" + hits
                    + " expected=" + specs[s].Expected;
                _report.Add(refusal);
                HammerFixPlugin.Log.LogError(refusal
                    + " - the method keeps the vanilla grid literals, because a partly "
                    + "rewritten row*width+col map mis-maps every icon.");
                return list;
            }

            for (int s = 0; s < specs.Length; s++)
            {
                var provider = Self(specs[s].Provider);
                for (int i = 0; i < list.Count; i++)
                {
                    if (!list[i].LoadsConstant(specs[s].Value)) continue;
                    var call = new CodeInstruction(SRE.OpCodes.Call, provider);
                    call.labels.AddRange(list[i].labels);
                    call.blocks.AddRange(list[i].blocks);
                    list[i] = call;
                }
                string done = where + ":" + specs[s].Value + " -> " + specs[s].Provider
                    + "() x" + specs[s].Expected;
                _report.Add(done);
                HammerFixPlugin.Log.LogInfo("rewrote " + done);
            }
            return list;
        }

        // ------------------------------------------------------------------
        // Sizing, from the pass that knows what is available.
        // ------------------------------------------------------------------
        private static SR.FieldInfo _byCat;

        private static SC.List<SC.List<Piece>> AvailableByCategory(PieceTable pt)
        {
            if (_byCat == null)
                _byCat = typeof(PieceTable).GetField("m_availablePiecesByCategory",
                    SR.BindingFlags.Instance | SR.BindingFlags.Public | SR.BindingFlags.NonPublic);
            return _byCat == null ? null : (SC.List<SC.List<Piece>>)_byCat.GetValue(pt);
        }

        private static void MeasurePostfix(PieceTable __instance)
        {
            if (__instance == null) return;
            var by = AvailableByCategory(__instance);
            if (by == null) return;
            int max = 0;
            for (int i = 0; i < by.Count; i++)
            {
                var l = by[i];
                if (l != null && l.Count > max) max = l.Count;
            }
            if (max <= _fullestCategory) return;
            _fullestCategory = max;
            Resize(max);
        }

        /// <summary>
        /// Smallest grid inside the build panel's own clip that holds
        /// <paramref name="need"/> cells, with the cell no smaller than
        /// MinIconPixels. Exposed so a probe can drive it headlessly.
        ///
        /// The clip is Hud.m_pieceListMask. MEASURED on the shipped prefab it
        /// is 1050 x 416 with Hud.m_pieceIconSpacing 70 - exactly the vanilla
        /// 15 columns wide, and 5.94 cells tall for the 6 rows vanilla draws.
        /// Reading it rather than deriving it from 15x6 means a UI mod that
        /// resizes the panel is followed instead of contradicted; if it cannot
        /// be read, the vanilla 15x6 footprint at the measured spacing is the
        /// fallback, and if the spacing cannot be read either the grid is left
        /// alone. A guess is worse than no change.
        /// </summary>
        public static void Resize(int need)
        {
            int forceW = HammerFixPlugin.ForceWidth.Value;
            int forceH = HammerFixPlugin.ForceHeight.Value;
            if (forceW > 0 && forceH > 0)
            {
                Apply(forceW, forceH, (float)VanillaWidth / forceW, "forced by config", need);
                return;
            }

            float spacing = Spacing();
            if (spacing <= 0f)
            {
                Apply(VanillaWidth, VanillaHeight, 1f,
                    "build panel not measurable (Hud.m_pieceIconSpacing unreadable); grid left "
                    + "at vanilla rather than guessed", need);
                return;
            }

            float panelW, panelH;
            string source;
            if (!Panel(out panelW, out panelH))
            {
                panelW = spacing * VanillaWidth;
                panelH = spacing * VanillaHeight;
                source = "vanilla footprint (mask rect unreadable)";
            }
            else source = "mask " + panelW.ToString("0") + "x" + panelH.ToString("0");

            int minPx = HammerFixPlugin.MinIconPixels.Value;
            // Capacity is monotonic in the cell size, so walk the cell down from
            // the panel's own pitch and stop at the first size that fits.
            int bestW = VanillaWidth, bestH = VanillaHeight;
            float bestCell = spacing;
            for (float cell = spacing; cell >= minPx; cell -= 0.5f)
            {
                int cols = (int)UE.Mathf.Floor(panelW / cell);
                int rows = (int)UE.Mathf.Floor(panelH / cell);
                if (cols < 1 || rows < 1) break;
                bestW = cols; bestH = rows; bestCell = cell;
                if (cols * rows >= need) break;
            }
            if (bestW < VanillaWidth) bestW = VanillaWidth;
            if (bestH < VanillaHeight) bestH = VanillaHeight;

            Apply(bestW, bestH, bestCell / spacing,
                "cell " + bestCell.ToString("0.0") + " of the panel's " + spacing.ToString("0.0")
                + ", " + source, need);
        }

        /// <summary>The build panel's own clip rect, in its own units.</summary>
        private static bool Panel(out float w, out float h)
        {
            w = 0f; h = 0f;
            var hud = Hud.instance;
            if (hud == null) return false;
            try
            {
                if (_mask == null)
                    _mask = typeof(Hud).GetField("m_pieceListMask",
                        SR.BindingFlags.Instance | SR.BindingFlags.Public | SR.BindingFlags.NonPublic);
                var rt = _mask == null ? null : _mask.GetValue(hud) as UE.RectTransform;
                if (rt == null) return false;
                w = rt.rect.width;
                h = rt.rect.height;
                return w > 0f && h > 0f;
            }
            catch { return false; }
        }

        private static SR.FieldInfo _mask;

        private static void Apply(int w, int h, float scale, string why, int need)
        {
            _width = w;
            _height = h;
            _scale = scale;

            string grid = w + "x" + h;
            if (_reportedGrid != grid)
            {
                _reportedGrid = grid;
                HammerFixPlugin.Log.LogInfo("build grid " + grid + " = " + (w * h)
                    + " cells (vanilla " + VanillaWidth + "x" + VanillaHeight + " = "
                    + (VanillaWidth * VanillaHeight) + "), icon scale " + scale.ToString("0.000")
                    + ", " + why + "; fullest category needs " + need);
            }
            if (need > w * h && _reportedShortfall != need)
            {
                _reportedShortfall = need;
                HammerFixPlugin.Log.LogError("GRID SHORT BY " + (need - w * h)
                    + ": the fullest category has " + need + " available pieces and the largest "
                    + "grid that fits the build panel at MinIconPixels="
                    + HammerFixPlugin.MinIconPixels.Value + " holds " + (w * h)
                    + ". Those " + (need - w * h) + " pieces sit at indices with no icon and "
                    + "cannot be reached. Lower MinIconPixels, or move pieces to another "
                    + "category. This is a shortfall, not a fix.");
            }
        }

        private static float Spacing()
        {
            var hud = Hud.instance;
            if (hud == null) return 0f;
            try { return hud.m_pieceIconSpacing; } catch { return 0f; }
        }

        private static void ScalePostfix(Hud __instance)
        {
            if (__instance == null) return;
            var root = __instance.m_pieceListRoot;
            if (root == null) return;
            var s = root.localScale;
            if (UE.Mathf.Approximately(s.x, _scale) && UE.Mathf.Approximately(s.y, _scale)) return;
            root.localScale = new UE.Vector3(_scale, _scale, 1f);
        }

        /// <summary>Current grid, for reporting.</summary>
        public static void Current(out int w, out int h, out float scale)
        {
            w = _width; h = _height; scale = _scale;
        }
    }
}
