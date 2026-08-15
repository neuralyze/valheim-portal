using System;
using System.Reflection;
using HarmonyLib;

namespace NeuralyzeVRFixes
{
    // Why the build menu can be unreachable, and how to tell which link failed.
    //
    // Valheim's piece selection is opened by the ZInput button "BuildMenu". VHVR maps
    // that button to exactly one SteamVR action - laserPointers_RightClick - and that
    // action lives in the LaserPointers action set. VHVR activates the set only while
    // VRPlayer.activePointer is non-null (VRControls.updateLasersActionSetState), and a
    // pointer is only usable while shouldLaserPointersBeActive() holds, which is
    // UseVrControls && (Cursor.visible || InPlaceMode).
    //
    // The same physical button is deliberately shared with valheim_QuickSwitch, and
    // VHVR arbitrates by duration: a tap under 0.3s is meant to reach BuildMenu, a
    // longer hold opens the quick-switch radial. So "nothing happens on press" has three
    // very different causes that look identical in the headset:
    //
    //   inPlaceMode=False              -> no build tool equipped; the button was never eligible
    //   inPlaceMode=True  pointer=False -> the pointer never activated, so the action set is
    //                                     inactive and the press cannot reach the game
    //   pointer=True laserSet=True     -> the press does reach the game; if pieceMenu stays
    //                                     False the menu is refusing to open, and if it goes
    //                                     True the menu opened somewhere invisible
    //
    // Every value is read reflectively: this plugin is compiled against Valheim and
    // BepInEx, not against VHVR, so a VHVR update that renames or moves any of these
    // must degrade to "unknown" rather than throwing every frame.
    internal static class BuildMenuProbe
    {
        private const string Unknown = "?";

        private static bool _resolved;
        private static PropertyInfo _activePointer;   // VRPlayer.activePointer
        private static PropertyInfo _laserControls;   // VRControls.laserControlsActive
        private static MethodInfo _pieceVisible;      // Hud.IsPieceSelectionVisible()

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            try
            {
                Type vrPlayer = TypeCache.Get("ValheimVRMod.VRCore.VRPlayer");
                if (vrPlayer != null)
                    _activePointer = vrPlayer.GetProperty("activePointer", BindingFlags.Static | BindingFlags.Public);

                Type vrControls = TypeCache.Get("ValheimVRMod.VRCore.UI.VRControls");
                if (vrControls != null)
                    _laserControls = vrControls.GetProperty("laserControlsActive", BindingFlags.Static | BindingFlags.Public);

                _pieceVisible = AccessTools.Method("Hud:IsPieceSelectionVisible");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "build menu probe unavailable: " + e.Message);
            }
        }

        // True when VHVR considers a laser pointer usable. With a build tool equipped
        // this must be true, or the button that opens the menu is inert.
        internal static string PointerActive()
        {
            Resolve();
            if (_activePointer == null) return Unknown;
            try { return (_activePointer.GetValue(null, null) != null).ToString(); }
            catch { return Unknown; }
        }

        // True when the action set holding BuildMenu is active. Follows PointerActive by
        // one frame, so a true/false split between them narrows the failure to VHVR's
        // set bookkeeping rather than the pointer itself.
        internal static string LaserSetActive()
        {
            Resolve();
            if (_laserControls == null) return Unknown;
            try { return _laserControls.GetValue(null, null).ToString(); }
            catch { return Unknown; }
        }

        // Whether the piece selection window is open right now. This is what separates a
        // menu that never opens from one that opens out of sight. Hud.IsPieceSelectionVisible
        // is reached statically by VHVR but is declared on the instance in this build of the
        // game, so both shapes are supported rather than assuming either.
        internal static string PieceSelectionVisible()
        {
            Resolve();
            if (_pieceVisible == null) return Unknown;
            try
            {
                if (_pieceVisible.IsStatic) return _pieceVisible.Invoke(null, null).ToString();
                object hud = AccessTools.Field(typeof(Hud), "m_instance") != null
                    ? AccessTools.Field(typeof(Hud), "m_instance").GetValue(null)
                    : AccessTools.Property(typeof(Hud), "instance")?.GetValue(null, null);
                if (hud == null) return "noHud";
                return _pieceVisible.Invoke(hud, null).ToString();
            }
            catch { return Unknown; }
        }

        // The game's own condition for opening piece selection, from
        // Player.UpdateBuildGuiInput:
        //
        //     ZInput.GetButtonDown("BuildMenu") && !PlayerController.HasInputDelay
        //         && !Hud.InRadial()  ->  Hud.instance.TogglePieceSelection()
        //
        // All three are recorded on the frame the button reads down, because a press that
        // satisfies the first and fails either guard is indistinguishable in the headset
        // from a press that never arrived.
        private static bool _guardsResolved;
        private static MethodInfo _inRadial;      // Hud.InRadial()
        private static PropertyInfo _inputDelay;  // PlayerController.HasInputDelay
        private static MethodInfo _quickSelect;   // Hud.IsQuickPieceSelectEnabled()

        private static void ResolveGuards()
        {
            if (_guardsResolved) return;
            _guardsResolved = true;
            _inRadial = AccessTools.Method("Hud:InRadial");
            _quickSelect = AccessTools.Method("Hud:IsQuickPieceSelectEnabled");
            Type controller = TypeCache.Get("PlayerController");
            if (controller != null)
                _inputDelay = controller.GetProperty("HasInputDelay", BindingFlags.Static | BindingFlags.Public);
        }

        internal static bool InRadial()
        {
            ResolveGuards();
            if (_inRadial == null) return false;
            try { return (bool)_inRadial.Invoke(null, null); }
            catch { return false; }
        }

        private static string InputDelay()
        {
            ResolveGuards();
            if (_inputDelay == null) return Unknown;
            try { return _inputDelay.GetValue(null, null).ToString(); }
            catch { return Unknown; }
        }

        private static string QuickPieceSelect()
        {
            ResolveGuards();
            if (_quickSelect == null) return Unknown;
            try
            {
                object hud = AccessTools.Property(typeof(Hud), "instance")?.GetValue(null, null);
                return hud == null ? "noHud" : _quickSelect.Invoke(hud, null).ToString();
            }
            catch { return Unknown; }
        }

        private static int _pressLogged;

        // Called every frame. Reads the button exactly as the game does - the static
        // ZInput.GetButtonDown, not VHVR's patched helper - so a press that VHVR raises
        // but the game cannot see shows up as silence here.
        internal static void Tick()
        {
            try
            {
                if (_pressLogged >= 20) return;
                if (!ZInput.GetButtonDown("BuildMenu")) return;
                _pressLogged++;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "BuildMenu DOWN #" + _pressLogged
                    + " inRadial=" + InRadial()
                    + " hasInputDelay=" + InputDelay()
                    + " quickPieceSelect=" + QuickPieceSelect()
                    + " pieceMenu=" + PieceSelectionVisible());
            }
            catch { }
        }
    }
}
