using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Offline Companions draws its companion status panel next to the flat HUD minimap. VHVR moves the
    // minimap onto a wrist canvas (MinimapPanelPlacement=RightWrist), so converting the companion panel
    // in place leaves it floating where the flat minimap used to be - correct in world space, nowhere
    // near the map it belongs beside.
    //
    // VHVR builds its wrist canvases with a layout group and parents its own panel clones into them, so
    // the supported way to sit beside the minimap is to become another child of that canvas. That is
    // what this does. It is deliberately NOT a canvas adoption: a panel parented under the wrist canvas
    // inherits its world-space transform, and adopting it separately would give the same object two
    // owners fighting over its RectTransform.
    internal static class CompanionWristHud
    {
        internal const string HudHost = "HC_CompanionHudPanelHost";
        internal const string HudPanel = "HC_CompanionHudPanel";

        private static Type _vrHudType;
        private static PropertyInfo _instanceProp;
        private static bool _resolved;
        private static Transform _attachedTo;
        private static bool _warned;

        private static bool Enabled
        {
            get
            {
                string mode = NeuralyzeVRFixesPlugin.CompanionHudPlacement.Value;
                return !string.IsNullOrEmpty(mode) && !mode.Equals("Off", StringComparison.OrdinalIgnoreCase);
            }
        }

        // Names this component takes ownership of, so the canvas adopter leaves them alone.
        internal static void ClaimNames(HashSet<string> wanted)
        {
            if (!Enabled) return;
            wanted.Remove(HudHost);
            wanted.Remove(HudPanel);
        }

        private static bool Resolve()
        {
            if (_resolved) return _vrHudType != null;
            _vrHudType = AccessTools.TypeByName("ValheimVRMod.VRCore.UI.VRHud");
            if (_vrHudType != null)
                _instanceProp = AccessTools.Property(_vrHudType, "instance");
            _resolved = true;
            return _vrHudType != null;
        }

        // Returns the wrist canvas transform VHVR parents its own HUD panels into, or null while VHVR
        // has not built it yet. The field is private; VHVR exposes no accessor for it.
        private static Transform WristCanvas()
        {
            if (!Resolve() || _instanceProp == null) return null;
            object hud = _instanceProp.GetValue(null, null);
            if (hud == null) return null;
            bool left = NeuralyzeVRFixesPlugin.CompanionHudPlacement.Value
                .Equals("LeftWrist", StringComparison.OrdinalIgnoreCase);
            FieldInfo field = AccessTools.Field(_vrHudType, left ? "leftHudCanvas" : "rightHudCanvas");
            if (field == null) return null;
            Canvas canvas = field.GetValue(hud) as Canvas;
            return canvas == null ? null : canvas.transform;
        }
        internal static void Tick()
        {
            if (!Enabled) return;
            // On a Flat client VRHud does not exist, and the panel is already where the mod wants it.
            // Bailing here keeps the 2 Hz GameObject.Find off non-VR installs entirely.
            if (!Resolve()) return;
            try
            {
                GameObject host = GameObject.Find(HudHost) ?? GameObject.Find(HudPanel);
                if (host == null)
                {
                    _attachedTo = null;   // panel gone; re-attach whenever it comes back
                    return;
                }
                if (_attachedTo != null && host.transform.parent == _attachedTo) return;

                Transform wrist = WristCanvas();
                if (wrist == null)
                {
                    if (!_warned)
                    {
                        _warned = true;
                        NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                            + "companion HUD: VHVR wrist canvas not available; leaving the panel where it is");
                    }
                    return;
                }

                string before = host.transform.parent == null ? "<root>" : host.transform.parent.name;
                host.transform.SetParent(wrist, false);
                // The wrist canvas is scaled to metres (0.1 / rect.width); a panel authored in HUD
                // pixels inherits that, so no additional scaling is applied here on purpose. Any
                // offset the operator wants is the mod's own HC_HudPanelOffsetX/Y.
                host.transform.localRotation = Quaternion.identity;
                _attachedTo = wrist;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "companion HUD parented to " + wrist.name + " (was " + before + ")");
            }
            catch (Exception e)
            {
                if (_warned) return;
                _warned = true;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "companion HUD attach failed: " + e.Message);
            }
        }
    }
}
