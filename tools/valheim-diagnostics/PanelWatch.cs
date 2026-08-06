using System;
using System.Reflection;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace ValheimDiagnostics
{
    // Measures the GUI panel INSIDE the render loop, which is the one place the
    // previous probe could not look.
    //
    // VRGUI.updateUiPanelScaleAndPosition is driven from OnRenderObject, and Unity
    // calls OnRenderObject once per rendering camera. VR has several (left eye, right
    // eye, the GUI screen-space camera, the UI panel camera, the world-space UI
    // camera, the skybox camera), so the panel transform can be rewritten between the
    // two eye renders. The left and right eye would then see the HUD in different
    // places, which reads as flicker and cannot occur in flat play with one camera.
    //
    // Sampling from Update() once per frame is blind to this: it always observes the
    // same point in the loop, so an intra-frame excursion that settles before the next
    // frame looks perfectly static. That is why the earlier probe reported dPos ~ 0.
    internal static class PanelWatch
    {
        internal static ManualLogSource Log;
        private static FieldInfo _panelField;
        private static object _instance;
        private static bool _installed;

        // Intra-frame accumulators.
        private static int _frame = -1;
        private static int _callsThisFrame;
        private static bool _haveFirst;
        private static Vector3 _firstPos;
        private static Quaternion _firstRot;
        private static float _spreadPos, _spreadRot;

        // Per-report maxima.
        private static int _maxCalls, _framesWithMultipleCalls, _frames;
        private static float _maxSpreadPos, _maxSpreadRot;
        private static float _nextReport;

        // Counts how often AzuEPI tears down and rebuilds the quick-slot bar's icon
        // objects. ClearElements(true) calls Object.Destroy on each element, which Unity
        // defers to end of frame, so every call opens a window where those icons do not
        // exist. At 90 fps that gap is invisible; at 20 fps with reprojection it is a
        // ~50 ms hole, repeated - which matches a bar that flickers while the vanilla
        // status icons beside it do not.
        private static int _clearCalls, _destroyingClears;
        private static float _nextBarReport;

        private static void AfterClearElements(bool destroy)
        {
            _clearCalls++;
            if (destroy) _destroyingClears++;
            if (Time.unscaledTime < _nextBarReport) return;
            _nextBarReport = Time.unscaledTime + 1f;
            int children = -1;
            try
            {
                if (Hud.instance != null)
                {
                    Transform root = Hud.instance.transform.Find("hudroot");
                    Transform bar = root == null ? null : root.Find("AzuEPI_QuickAccessBar");
                    if (bar != null) children = bar.childCount;
                }
            }
            catch { }
            Log.LogInfo(string.Format("[BarWatch] clearsPerSec={0} destroyingClears={1} barChildren={2} frame={3}",
                _clearCalls, _destroyingClears, children, Time.frameCount));
            _clearCalls = 0; _destroyingClears = 0;
        }

        internal static void Install(Harmony harmony, ManualLogSource log)
        {
            Log = log;
            if (_installed) return;
            _installed = true;
            try
            {
                MethodInfo clear = AccessTools.Method("AzuEPI.Game.Slots.QAB.QuickAccessBar:ClearElements");
                if (clear == null)
                {
                    log.LogWarning("[BarWatch] QuickAccessBar.ClearElements not found - bar rebuild counting disabled");
                }
                else
                {
                    harmony.Patch(clear, postfix: new HarmonyMethod(typeof(PanelWatch).GetMethod(nameof(AfterClearElements), BindingFlags.Static | BindingFlags.NonPublic)));
                    log.LogInfo("[BarWatch] installed on QuickAccessBar.ClearElements");
                }
            }
            catch (Exception e) { log.LogWarning("[BarWatch] install failed: " + e.Message); }
            try
            {
                MethodInfo target = AccessTools.Method("ValheimVRMod.VRCore.UI.VRGUI:updateUiPanelScaleAndPosition");
                if (target == null)
                {
                    Log.LogWarning("[PanelWatch] VRGUI.updateUiPanelScaleAndPosition not found - intra-frame sampling disabled");
                    return;
                }
                Type vrGui = target.DeclaringType;
                _panelField = AccessTools.Field(vrGui, "_uiPanel");
                if (_panelField != null && !_panelField.IsStatic)
                {
                    UnityEngine.Object[] found = UnityEngine.Object.FindObjectsOfType(vrGui);
                    if (found != null && found.Length > 0) _instance = found[0];
                }
                harmony.Patch(target, postfix: new HarmonyMethod(typeof(PanelWatch).GetMethod(nameof(AfterUpdatePanel), BindingFlags.Static | BindingFlags.NonPublic)));
                Log.LogInfo("[PanelWatch] installed on VRGUI.updateUiPanelScaleAndPosition staticPanel=" + (_panelField != null && _panelField.IsStatic));
            }
            catch (Exception e)
            {
                Log.LogWarning("[PanelWatch] install failed: " + e.Message);
            }
        }

        private static void AfterUpdatePanel()
        {
            try
            {
                Transform panel = _panelField == null ? null : _panelField.GetValue(_panelField.IsStatic ? null : _instance) as Transform;
                if (panel == null) return;
                int frame = Time.frameCount;
                if (frame != _frame)
                {
                    // Frame boundary: fold the finished frame into the report window.
                    if (_frame >= 0)
                    {
                        _frames++;
                        if (_callsThisFrame > _maxCalls) _maxCalls = _callsThisFrame;
                        if (_callsThisFrame > 1) _framesWithMultipleCalls++;
                        if (_spreadPos > _maxSpreadPos) _maxSpreadPos = _spreadPos;
                        if (_spreadRot > _maxSpreadRot) _maxSpreadRot = _spreadRot;
                    }
                    _frame = frame;
                    _callsThisFrame = 0;
                    _spreadPos = _spreadRot = 0f;
                    _haveFirst = false;
                }
                _callsThisFrame++;
                if (!_haveFirst)
                {
                    _firstPos = panel.position;
                    _firstRot = panel.rotation;
                    _haveFirst = true;
                }
                else
                {
                    // Distance from the first placement this frame: the disagreement
                    // between one camera's view of the panel and another's.
                    float dPos = (panel.position - _firstPos).magnitude;
                    float dRot = Quaternion.Angle(panel.rotation, _firstRot);
                    if (dPos > _spreadPos) _spreadPos = dPos;
                    if (dRot > _spreadRot) _spreadRot = dRot;
                }
                Report();
            }
            catch { }
        }

        private static void Report()
        {
            if (Time.unscaledTime < _nextReport) return;
            _nextReport = Time.unscaledTime + 1f;
            Log.LogInfo(string.Format(
                "[PanelWatch] frames={0} maxCallsPerFrame={1} framesMultiCall={2} intraFrameMaxPos={3:F6} intraFrameMaxRot={4:F4} cameras={5}",
                _frames, _maxCalls, _framesWithMultipleCalls, _maxSpreadPos, _maxSpreadRot, Camera.allCamerasCount));
            _frames = 0; _maxCalls = 0; _framesWithMultipleCalls = 0;
            _maxSpreadPos = _maxSpreadRot = 0f;
        }
    }
}
