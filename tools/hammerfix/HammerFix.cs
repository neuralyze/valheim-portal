// HammerFix - a BepInEx plugin of ours that repairs the hammer build menu.
//
// It is a BRIDGE, not a mod swap, in the same sense as tools/everybodyshim: it
// owns nothing of the build menu, it only makes two pieces of code that are
// already installed behave the way they were written to behave on the game
// they are now running on.
//
// ===========================================================================
// DEFECT 1 - an unresolvable field reference in a bundled PieceManager copy
// ===========================================================================
// MEASURED, Mono.Cecil over the live Ulfsland plugin set (119 DLLs), field
// layout read from assembly_valheim.dll of that same install:
//
//   PieceTable.m_availablePieces            HashSet<Piece>      public initonly
//   PieceTable.m_availablePiecesByCategory  List<List<Piece>>   private
//
// `m_availablePieces` is NEW in Valheim 1.0 and TOOK THE NAME. The pre-1.0
// List<List<Piece>> that used to own it is now m_availablePiecesByCategory.
//
// TEN loaded assemblies define a type named PiecePrefabManager here - not six.
// They are NOT the same library version; per-assembly Cecil scan of
// `ldfld PieceTable::m_availablePieces : List<List<Piece>>`:
//
//   OdinUndercroft            5 pre-1.0 refs   0 correct   BROKEN
//   RavenwoodRestorations     5                0           BROKEN
//   Basements                 5                0           BROKEN
//   OdinsHorsePen             5                0           BROKEN
//   CraftyCartsRemake1_patched 0               5           correct, same shape
//   OdinsKingdom              0                2           correct, newer shape
//   OdinsFoodBarrels          0                2           correct, newer shape
//   OdinCampsite              0                2           correct, newer shape
//   OdinShip                  0                2           correct, newer shape
//   AdventureBackpacks        0                0           no UpdateAvailable patch
//
// Every copy's PiecePrefabManager::.cctor unconditionally runs
//   harmony.Patch(PieceTable.UpdateAvailable, transpiler: UpdateAvailable_Transpiler)
//   harmony.Patch(PieceTable.UpdateAvailable, prefix: UpdateAvailable_Prefix,
//                                             postfix: UpdateAvailable_Postfix)
// under the shared Harmony id `org.bepinex.helpers.PieceManager`. So the four
// broken prefixes are installed on the same method as the four correct ones,
// and a Harmony prefix chain runs every prefix: one throwing prefix aborts the
// whole call, the original included.
//
// What the prefix is FOR is the sizing that vanilla only does once:
//
//   PieceTable::UpdateAvailable, IL_0021..IL_003e
//     if (m_availablePiecesByCategory.Count == 0)
//         for (i = 0; i < 9; i++) m_availablePiecesByCategory.Add(new List<Piece>());
//   ...IL_014e
//     m_availablePiecesByCategory[(int)piece.m_category].Add(piece);
//
// `9` is `(int)Piece.PieceCategory.Max`; UpdateAvailable_Transpiler rewrites
// every load of it to `ModifiedMaxCategory()`, which is
// `Enum.GetValues(typeof(Piece.PieceCategory)).Length - 1` under the library's
// own Enum.GetValues/GetNames patches. But the `Count == 0` guard means
// vanilla sizes the list ONCE EVER, so growth after that first call is the
// prefix's job alone - and that is the method with the dead field reference.
//
// Also measured, PieceTable::.ctor: m_selectedPiece and m_lastSelectedPiece are
// `new Vector2Int[9]` and m_selectedCategory is 9. Every category-indexed read
// of those arrays (GetSelectedPiece, SetSelected, Right/Left/Up/DownPiece)
// therefore needs them resized too - which is what UpdateAvailable_Postfix
// does, in the same broken copies, off the same dead field.
//
// The one shim that could have supplied the missing name REFUSES, and is right
// to: Valheim10Compatibility logs `BLOCKED PieceTable.m_availablePieces
// (List<List<Piece>>, alias of m_availablePiecesByCategory)` because two
// same-named fields make Type.GetField and Traverse.Field throw
// AmbiguousMatchException for every plugin in the fleet. The fix belongs on
// the consumer side, which is here.
//
// WHAT THIS PLUGIN DOES ABOUT IT, and what it deliberately does not:
//   * It does NOT catch the exception. A swallowed throw leaves the
//     availability pass just as aborted as an unswallowed one.
//   * It CLASSIFIES each installed UpdateAvailable_Prefix/_Postfix by ASKING
//     THE RUNTIME, not by modelling it: each is invoked once against a
//     throwaway PieceTable owned by this plugin. Mono, not our reading of
//     Mono's field resolver, decides whether that field reference resolves.
//     Only the ones that actually throw are removed, by Harmony.Unpatch of
//     that exact MethodInfo - never by Harmony id, which would take the four
//     correct copies and the transpiler with them.
//   * It then does the sizing itself, correctly and on EVERY call, from a
//     prefix at Priority.First. That is what makes the result load-order
//     independent: it no longer matters whether the enum finished growing
//     before the first UpdateAvailable.
//
// Patching is by TYPE NAME across every loaded assembly. The library is
// bundled, not referenced, so there is no assembly identity to target.
//
// ===========================================================================
// DEFECT 2 - the 15x6 icon grid
// ===========================================================================
// MEASURED in assembly_valheim.dll. `PieceTable.m_gridWidth`/`m_gridHeight` are
// `static literal` - compile-time constants with no runtime storage - so the
// grid exists only as inlined literals at eight sites:
//
//   Hud::UpdatePieceList        ldc.i4.s 15 -> stloc.1 ; ldc.i4.6 -> stloc.2
//                               icon index = row*width + col
//   Hud::GetSelectedGrid        ldc.i4.s 15 -> stloc.0 ; ldc.i4.6 -> stloc.1
//   PieceTable::GetPiece        ldc.i4.s 15   (p.y*15 + p.x)
//   PieceTable::GetPieceIndex   ldc.i4.s 15   x3  (i%15, (i - i%15)/15)
//   PieceTable::RightPiece      ldc.i4.s 15   (wrap x at width)
//   PieceTable::LeftPiece       ldc.i4.s 14   (wrap x to width-1)
//   PieceTable::DownPiece       ldc.i4.6      (wrap y at height)
//   PieceTable::UpPiece         ldc.i4.5      (wrap y to height-1)
//
// All eight are transpiled here, from one pair of providers, so row*width+col
// cannot drift between the Hud that builds the icons and the PieceTable that
// resolves a click. Nothing else in assembly_valheim indexes m_pieceIcons: the
// only consumers are UpdatePieceList (builds and fills) and GetSelectedGrid
// (reverse lookup), both rewritten from the same providers, and UpdatePieceList
// rebuilds the list whenever `m_pieceIcons.Count != width*height`.
//
// THE GRID CANNOT BE RAISED BLIND. Icons are placed at
// `anchoredPosition = (col*m_pieceIconSpacing, -row*m_pieceIconSpacing)` inside
// Hud.m_pieceListRoot and nothing resizes the panel, so extra columns run right
// and extra rows run down, into or past Hud.m_pieceListMask - a serialized
// RectTransform with ZERO code references in assembly_valheim, i.e. purely a
// prefab-side clip. A bigger grid whose cells land outside it is not a fix.
//
// So the dimensions are MEASURED AT RUNTIME off that panel, not hardcoded:
// see BuildGrid.Resolve. The icon scale is lowered only as far as
// MinIconPixels, the grid is whatever fits at that scale, and if the result
// still cannot hold the fullest category the shortfall is LOGGED with the
// number of pieces it cannot reach. A shrink must not be silent.
//
// If the panel cannot be measured, the grid stays at vanilla 15x6. A guess is
// worse than no change.
//
// ===========================================================================
// THIRD CANDIDATE - trailing categories with no tab object: REFUTED
// ===========================================================================
// MEASURED, Hud::UpdateBuild IL_0084..IL_014e: the tab loop is
//   for (i = 0; i < m_pieceCategoryTabs.Length; i++)
//       tabs[i].SetActive(i < pieceTable.m_categories.Count);
// so a category at index >= m_pieceCategoryTabs.Length would get no tab and no
// exception - it simply would not be there. The array is sized by
// PiecePrefabManager::CreateCategoryTabs to exactly ModifiedMaxCategory().
//
// It is not short. MEASURED in the loaded process with the full plugin set:
//   Hud.m_pieceCategoryTabs.Length      106
//   _HammerPieceTable.m_categories      6     (the six vanilla tabs)
//   distinct category ids in use        16, the highest 105
//   ModifiedMaxCategory()               106 from the three newest copies,
//                                        27 from the six older ones
// Even the pessimistic 27 exceeds every category count any table here
// declares. NOTHING IS ADDED FOR THIS. A fix for a defect that is not
// happening is worse than no fix, so the repair drafted for it was deleted
// rather than shipped untested.
using S = global::System;
using SC = global::System.Collections.Generic;
using SR = global::System.Reflection;
using UE = global::UnityEngine;
using HL = global::HarmonyLib;

namespace Neuralyze.HammerFix
{
    [BepInEx.BepInPlugin(GUID, "HammerFix", "1.0.0")]
    public class HammerFixPlugin : BepInEx.BaseUnityPlugin
    {
        public const string GUID = "neuralyze.hammerfix";

        internal static BepInEx.Logging.ManualLogSource Log;
        internal static HL.Harmony Harmony;

        internal static BepInEx.Configuration.ConfigEntry<bool> RepairPass;
        internal static BepInEx.Configuration.ConfigEntry<bool> RaiseGrid;
        internal static BepInEx.Configuration.ConfigEntry<int> MinIconPixels;
        internal static BepInEx.Configuration.ConfigEntry<int> ForceWidth;
        internal static BepInEx.Configuration.ConfigEntry<int> ForceHeight;

        private void Awake()
        {
            Log = Logger;

            RepairPass = Config.Bind("1 - availability pass", "Repair", true,
                "Remove PieceManager UpdateAvailable patches whose field reference does not "
                + "resolve on this game version, and size m_availablePiecesByCategory correctly "
                + "on every pass. Turning this off restores the broken behaviour exactly.");
            RaiseGrid = Config.Bind("2 - build grid", "Raise", true,
                "Raise the 15x6 icon grid to whatever the measured build panel can hold.");
            MinIconPixels = Config.Bind("2 - build grid", "MinIconPixels", 32,
                new BepInEx.Configuration.ConfigDescription(
                    "Smallest icon cell, in the build panel's own units, the grid may shrink to "
                    + "in order to fit more pieces. MEASURED: the panel's own cell "
                    + "(Hud.m_pieceIconSpacing) is 70 on the shipped prefab and its clip "
                    + "(Hud.m_pieceListMask) is 1050x416, i.e. exactly 15x6 of those cells. "
                    + "Lower means more cells and smaller icons.",
                    new BepInEx.Configuration.AcceptableValueRange<int>(16, 128)));
            ForceWidth = Config.Bind("2 - build grid", "ForceWidth", 0,
                "Override the measured column count. 0 = measure. Set both Force values to "
                + "pin an exact grid; the panel is then scaled to fit it if it can be.");
            ForceHeight = Config.Bind("2 - build grid", "ForceHeight", 0,
                "Override the measured row count. 0 = measure.");

            Harmony = new HL.Harmony(GUID);

            // Both at Message rather than Info. MEASURED in the operator's installed
            // BepInEx.cfg: [Logging.Disk] LogLevels = "Fatal, Error, Warning, Message"
            // and [Logging.Console] Enabled = false, so an Info line reaches no file and
            // no screen - and BepInEx's own "Loading [...]" notice is Info too, which is
            // why that install's LogOutput.log carries 595 Warning and 15 Message lines
            // and not one Info line. These two are how anyone reading that log learns the
            // plugin loaded at all and which of its two repairs are enabled.
            if (RepairPass.Value)
            {
                AvailabilityPass.Install(Harmony);
                Log.LogMessage("availability pass repair installed");
            }
            if (RaiseGrid.Value)
            {
                BuildGrid.Install(Harmony);
                Log.LogMessage("build grid raise installed");
            }
        }
    }
}
