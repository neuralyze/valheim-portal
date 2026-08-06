using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;

namespace NeuralyzeVRFixes
{
    // Decides whether a wrist-menu entry is relevant right now.
    //
    // The point is to stop the ring turning into a forty-button wall. 44 of the installed packages
    // expose keyboard-only features, but almost none of them are relevant all the time: quick-stack
    // matters at a chest, grid planting matters while placing, admin commands matter to an admin.
    // A permanently visible button for each is worse than no button, because finding the one you
    // want becomes the problem.
    //
    // No new plumbing is needed. VHVR already rebuilds the wrist strip on the events that change
    // context - Inventory.Changed, Humanoid.UnequipItem and InventoryGui.OnSelectedItem all call
    // refreshItems() - and MiscMenu filters the entry list on every rebuild. So a predicate
    // evaluated at rebuild time is sufficient: open a chest and the container actions appear, close
    // it and they go.
    //
    // Every predicate is resolved reflectively and reports itself once, because a predicate that
    // silently returns false is indistinguishable from a context that is genuinely inactive - the
    // exact failure mode that hid the admin console for three releases.
    internal static class MenuContext
    {
        private const string Always = "always";

        private static MethodInfo _containerOpen, _inventoryVisible, _isDead, _inPlaceMode;
        private static PropertyInfo _inventoryGuiInstance;
        private static FieldInfo _localPlayer;
        private static Type _player;
        private static bool _resolved;

        // name -> whether the predicate is known to this build, reported once at first use
        private static readonly Dictionary<string, bool> _reported = new Dictionary<string, bool>();

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;

            Type gui = AccessTools.TypeByName("InventoryGui");
            if (gui != null)
            {
                _inventoryGuiInstance = gui.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                // IsContainerOpen is an instance method on InventoryGui; VHVR itself gates on it
                // (ControlPatches.cs:983, VRGUI.cs:492), so it is a supported predicate rather than
                // an internal detail being borrowed.
                _containerOpen = AccessTools.Method(gui, "IsContainerOpen");
                _inventoryVisible = AccessTools.Method(gui, "IsVisible");
            }

            _player = AccessTools.TypeByName("Player");
            if (_player != null)
            {
                _localPlayer = AccessTools.Field(_player, "m_localPlayer");
                _isDead = AccessTools.Method(_player, "IsDead");
                // Placement/build mode: the method name has moved between versions, so try the
                // known spellings rather than assuming one.
                _inPlaceMode = AccessTools.Method(_player, "InPlaceMode")
                               ?? AccessTools.Method(_player, "IsPlacementMode")
                               ?? AccessTools.Method(_player, "InPlacementMode");
            }
        }

        internal static IEnumerable<string> Known()
        {
            return new[] { Always, "container", "inventory", "build", "admin", "alive" };
        }

        // Unknown predicates deliberately return TRUE. A typo in the config should surface the
        // button with a warning, not silently delete it - a missing button is the hardest failure
        // to diagnose from a headset.
        internal static bool Active(string predicate)
        {
            if (string.IsNullOrEmpty(predicate) || predicate == Always) return true;
            Resolve();
            switch (predicate)
            {
                case "container": return Report(predicate, _containerOpen != null, GuiBool(_containerOpen));
                case "inventory": return Report(predicate, _inventoryVisible != null, GuiBool(_inventoryVisible));
                case "build": return Report(predicate, _inPlaceMode != null, PlayerBool(_inPlaceMode));
                case "alive": return Report(predicate, _isDead != null, !PlayerBool(_isDead));
                case "admin": return Report(predicate, true, AdminCheck.IsAdmin());
                default:
                    Report(predicate, false, true);
                    return true;
            }
        }

        private static bool GuiBool(MethodInfo method)
        {
            if (method == null) return false;
            try
            {
                if (method.IsStatic) return Convert.ToBoolean(method.Invoke(null, null));
                object gui = _inventoryGuiInstance == null ? null : _inventoryGuiInstance.GetValue(null, null);
                return gui != null && Convert.ToBoolean(method.Invoke(gui, null));
            }
            catch { return false; }
        }

        private static bool PlayerBool(MethodInfo method)
        {
            if (method == null) return false;
            try
            {
                object local = _localPlayer == null ? null : _localPlayer.GetValue(null);
                return local != null && Convert.ToBoolean(method.Invoke(local, null));
            }
            catch { return false; }
        }

        private static bool Report(string predicate, bool resolvable, bool value)
        {
            bool seen;
            if (!_reported.TryGetValue(predicate, out seen))
            {
                _reported[predicate] = true;
                if (!resolvable)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "menu context '" + predicate + "' is not available in this build"
                        + " - entries using it will always show. Known: " + string.Join(", ", new List<string>(Known()).ToArray()));
                }
                else
                {
                    NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                        + "menu context '" + predicate + "' resolved, first read = " + value);
                }
            }
            return resolvable && value;
        }
    }
}
