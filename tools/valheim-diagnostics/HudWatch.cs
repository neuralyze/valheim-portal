using System;
using System.Collections.Generic;
using System.Reflection;
using BepInEx.Logging;
using UnityEngine;

namespace ValheimDiagnostics
{
    // Measures the HUD every frame instead of reasoning about it.
    //
    // Five config fixes for a "flickering HUD" were derived from decompiled IL and
    // all five failed, because static analysis can only rank candidates - it cannot
    // say which one is happening. This samples the actual objects once per frame and
    // reports the worst change seen per second, so the next decision is made from a
    // measurement.
    //
    // Deliberately category-agnostic: it does not assume the flicker is the panel
    // moving. It separately tracks the world-space quad's transform, the HUD canvas
    // size, the render texture, the canvas alpha, and the AzuEPI bar, so the log
    // says which of those is unstable - or that none of them is, which would rule
    // out this whole family of explanations.
    internal sealed class HudWatch
    {
        private readonly ManualLogSource _log;
        private bool _resolved;
        private bool _available;

        // VHVR reflection targets, resolved by name so the probe never needs to be
        // compiled or versioned against ValheimVRMod.
        private object _vrGuiInstance;
        private FieldInfo _uiPanelField;
        private FieldInfo _guiTextureField;
        private FieldInfo _isRecenteringField;
        private MethodInfo _dynamicGuiMethod;

        private Transform _panel;
        // Canvas and CanvasGroup live in UnityEngine.UIModule, which is not among the
        // available reference assemblies, so they are handled reflectively.
        private Component _hudCanvas;
        private Component _hudGroup;
        private PropertyInfo _renderModeProp, _worldCameraProp, _alphaProp;
        private Type _canvasType, _canvasGroupType;
        private RectTransform _hudRect;
        private RectTransform _barRect;

        // Per-frame accumulators, reset each report window.
        private bool _havePrev;
        private Vector3 _prevPos, _prevScale, _prevBarPos, _prevHudSize;
        private Quaternion _prevRot;
        private float _prevAlpha;
        private float _maxPosDelta, _maxRotDelta, _maxScaleDelta, _maxBarDelta, _maxAlphaDelta, _maxHudSizeDelta;
        private int _frames, _posChanges, _barChanges, _sizeChanges, _alphaChanges;
        private float _nextReport;

        // Quality-setting churn: QualitySettings.masterTextureLimit and lodBias are
        // rewritten at runtime by ValheimOptimizer's MasterLoop based on average frame
        // time. Changing the global texture mip level mid-session is directly visible,
        // so these are tracked as first-class suspects alongside the HUD transform.
        private int _prevTexLimit = int.MinValue, _texChanges, _texMin, _texMax;
        private float _prevLodBias = float.NaN, _lodChanges;
        private bool _haveQuality;
        // Angle between the GUI panel and where the player is actually looking.
        // This is the objective form of "does the HUD stick in view": 0 means dead
        // ahead, and a value that grows without being pulled back is drift.
        private Camera _vrCamera;
        private float _maxHeadAngle, _lastHeadAngle;
        // Yaw and pitch reported separately: a combined angle conflates the leftward
        // bias (yaw) with the panel sitting below eye level (pitch), and only the
        // former is what "too far left" refers to.
        private float _headYaw, _headPitch;

        private const float Epsilon = 1e-5f;
        private const float ReportSeconds = 1f;

        internal HudWatch(ManualLogSource log) { _log = log; }

        private void Resolve()
        {
            _resolved = true;
            Type vrGui = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    Type candidate = assembly.GetType("ValheimVRMod.VRCore.UI.VRGUI", false);
                    if (candidate != null) { vrGui = candidate; break; }
                }
                catch { }
            }
            if (vrGui == null)
            {
                _log.LogWarning("[HudWatch] ValheimVRMod.VRCore.UI.VRGUI not found - VR HUD sampling disabled");
                return;
            }

            const BindingFlags Any = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
            _uiPanelField = vrGui.GetField("_uiPanel", Any);
            _guiTextureField = vrGui.GetField("_guiTexture", Any);
            _isRecenteringField = vrGui.GetField("isRecentering", Any);
            _dynamicGuiMethod = vrGui.GetMethod("useDynamicallyPositionedGui", Any);

            // VRGUI is a MonoBehaviour; the panel field may be static or instance.
            if (_uiPanelField != null && !_uiPanelField.IsStatic)
            {
                UnityEngine.Object[] found = UnityEngine.Object.FindObjectsOfType(vrGui);
                if (found != null && found.Length > 0) _vrGuiInstance = found[0];
            }

            List<string> missing = new List<string>();
            if (_uiPanelField == null) missing.Add("_uiPanel");
            if (_guiTextureField == null) missing.Add("_guiTexture");
            if (_dynamicGuiMethod == null) missing.Add("useDynamicallyPositionedGui");
            const BindingFlags Pub = BindingFlags.Instance | BindingFlags.Public;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    if (_canvasType == null) _canvasType = assembly.GetType("UnityEngine.Canvas", false);
                    if (_canvasGroupType == null) _canvasGroupType = assembly.GetType("UnityEngine.CanvasGroup", false);
                }
                catch { }
            }
            if (_canvasType != null)
            {
                _renderModeProp = _canvasType.GetProperty("renderMode", Pub);
                _worldCameraProp = _canvasType.GetProperty("worldCamera", Pub);
            }
            if (_canvasGroupType != null) _alphaProp = _canvasGroupType.GetProperty("alpha", Pub);
            _available = _uiPanelField != null;
            _log.LogInfo("[HudWatch] resolved VRGUI"
                + " instance=" + (_vrGuiInstance != null)
                + " staticPanel=" + (_uiPanelField != null && _uiPanelField.IsStatic)
                + (missing.Count > 0 ? " missing=[" + string.Join(",", missing.ToArray()) + "]" : ""));
        }

        private static Vector3 SizeOf(RectTransform rect)
        {
            if (rect == null) return Vector3.zero;
            Rect r = rect.rect;
            return new Vector3(r.width, r.height, 0f);
        }

        private void Rebind()
        {
            if (_available)
            {
                try { _panel = _uiPanelField.GetValue(_uiPanelField.IsStatic ? null : _vrGuiInstance) as Transform; }
                catch { _panel = null; }
            }
            if (_hudCanvas == null && _canvasType != null && Hud.instance != null)
            {
                _hudCanvas = Hud.instance.GetComponentInParent(_canvasType);
                if (_hudCanvas != null)
                {
                    _hudRect = _hudCanvas.transform as RectTransform;
                    if (_canvasGroupType != null) _hudGroup = _hudCanvas.GetComponent(_canvasGroupType);
                }
            }
            if (_barRect == null && Hud.instance != null)
            {
                Transform root = Hud.instance.transform.Find("hudroot");
                if (root != null)
                {
                    Transform bar = root.Find("AzuEPI_QuickAccessBar");
                    if (bar != null) _barRect = bar as RectTransform;
                }
            }
        }

        // Called every frame. Cheap: a handful of transform reads.
        internal void Sample()
        {
            if (!_resolved) Resolve();
            Rebind();
            _frames++;

            Vector3 pos = _panel != null ? _panel.position : Vector3.zero;
            Quaternion rot = _panel != null ? _panel.rotation : Quaternion.identity;
            Vector3 scale = _panel != null ? _panel.localScale : Vector3.zero;
            Vector3 barPos = _barRect != null ? (Vector3)_barRect.anchoredPosition : Vector3.zero;
            Vector3 hudSize = SizeOf(_hudRect);
            float alpha = -1f;
            if (_hudGroup != null && _alphaProp != null)
            {
                try { alpha = Convert.ToSingle(_alphaProp.GetValue(_hudGroup, null)); } catch { }
            }

            if (_havePrev)
            {
                float dPos = (pos - _prevPos).magnitude;
                float dRot = Quaternion.Angle(rot, _prevRot);
                float dScale = (scale - _prevScale).magnitude;
                float dBar = (barPos - _prevBarPos).magnitude;
                float dSize = (hudSize - _prevHudSize).magnitude;
                float dAlpha = Mathf.Abs(alpha - _prevAlpha);
                if (dPos > _maxPosDelta) _maxPosDelta = dPos;
                if (dRot > _maxRotDelta) _maxRotDelta = dRot;
                if (dScale > _maxScaleDelta) _maxScaleDelta = dScale;
                if (dBar > _maxBarDelta) _maxBarDelta = dBar;
                if (dSize > _maxHudSizeDelta) _maxHudSizeDelta = dSize;
                if (dAlpha > _maxAlphaDelta) _maxAlphaDelta = dAlpha;
                if (dPos > Epsilon || dRot > Epsilon) _posChanges++;
                if (dBar > Epsilon) _barChanges++;
                if (dSize > Epsilon) _sizeChanges++;
                if (dAlpha > Epsilon) _alphaChanges++;
            }
            _prevPos = pos; _prevRot = rot; _prevScale = scale;
            _prevBarPos = barPos; _prevHudSize = hudSize; _prevAlpha = alpha;
            _havePrev = true;

            if (_vrCamera == null)
            {
                foreach (Camera cam in Camera.allCameras)
                {
                    if (cam != null && cam.name == "VRCamera") { _vrCamera = cam; break; }
                }
            }
            if (_panel != null && _vrCamera != null)
            {
                Vector3 panelFwd = _panel.forward, headFwd = _vrCamera.transform.forward;
                _headYaw = Vector3.SignedAngle(
                    new Vector3(headFwd.x, 0f, headFwd.z).normalized,
                    new Vector3(panelFwd.x, 0f, panelFwd.z).normalized, Vector3.up);
                _headPitch = Mathf.Asin(Mathf.Clamp(panelFwd.y, -1f, 1f)) * Mathf.Rad2Deg
                    - Mathf.Asin(Mathf.Clamp(headFwd.y, -1f, 1f)) * Mathf.Rad2Deg;
                _lastHeadAngle = Vector3.Angle(panelFwd, headFwd);
                if (_lastHeadAngle > _maxHeadAngle) _maxHeadAngle = _lastHeadAngle;
            }

            int texLimit = QualitySettings.masterTextureLimit;
            float lodBias = QualitySettings.lodBias;
            if (_haveQuality)
            {
                if (texLimit != _prevTexLimit) _texChanges++;
                if (Mathf.Abs(lodBias - _prevLodBias) > Epsilon) _lodChanges++;
            }
            else { _texMin = _texMax = texLimit; _haveQuality = true; }
            if (texLimit < _texMin) _texMin = texLimit;
            if (texLimit > _texMax) _texMax = texLimit;
            _prevTexLimit = texLimit; _prevLodBias = lodBias;

            if (Time.unscaledTime < _nextReport) return;
            _nextReport = Time.unscaledTime + ReportSeconds;
            Report();
        }

        private string RenderModeText()
        {
            if (_hudCanvas == null || _renderModeProp == null) return "none";
            try { return Convert.ToString(_renderModeProp.GetValue(_hudCanvas, null)); }
            catch { return "err"; }
        }

        private string WorldCameraText()
        {
            if (_hudCanvas == null || _worldCameraProp == null) return "noCam";
            try
            {
                Camera cam = _worldCameraProp.GetValue(_hudCanvas, null) as Camera;
                return cam != null ? cam.name : "null";
            }
            catch { return "err"; }
        }

        // Absolute position of the vanilla health panel: AzuEPI derives its bar
        // placement from it, so it is the reference point for an explicit override.
        private string HealthPanelPosition()
        {
            if (Hud.instance == null || Hud.instance.m_healthPanel == null) return "none";
            RectTransform rect = Hud.instance.m_healthPanel.transform as RectTransform;
            return rect != null ? rect.anchoredPosition.ToString("F1") : "none";
        }

        // One-time inventory of every HUD element, so an unidentified on-screen artifact
        // can be named from data instead of guessed at. Reports each descendant's path,
        // anchored position, size and active state; a stray dot shows up as a tiny or
        // oddly placed entry, and its path names the owning mod.
        private bool _dumped2;

        private void DumpHudTree()
        {
            if (_dumped2 || Hud.instance == null) return;
            _dumped2 = true;
            try
            {
                Transform root = Hud.instance.transform;
                int count = 0;
                foreach (RectTransform rect in root.GetComponentsInChildren<RectTransform>(true))
                {
                    if (rect == null) continue;
                    Rect r = rect.rect;
                    // Only report leaves that actually occupy space, plus anything suspiciously
                    // small, which is what an unexplained dot would look like.
                    bool tiny = r.width > 0f && r.width <= 40f && r.height > 0f && r.height <= 40f;
                    if (!tiny && rect.childCount > 0) continue;
                    string path = rect.name;
                    Transform walk = rect.parent;
                    int guard = 0;
                    while (walk != null && guard++ < 8) { path = walk.name + "/" + path; walk = walk.parent; }
                    _log.LogInfo(string.Format("[HudTree] {0}{1} pos={2} size={3:F0}x{4:F0} active={5} scale={6}",
                        tiny ? "TINY " : "", path, rect.anchoredPosition.ToString("F0"),
                        r.width, r.height, rect.gameObject.activeInHierarchy, rect.localScale.ToString("F2")));
                    if (++count > 220) break;
                }
                _log.LogInfo("[HudTree] reported " + count + " elements");
            }
            catch (Exception e) { _log.LogWarning("[HudTree] dump failed: " + e.Message); }
        }

        private void Report()
        {
            DumpHudTree();
            string dynamic = "?";
            if (_dynamicGuiMethod != null)
            {
                try { dynamic = Convert.ToString(_dynamicGuiMethod.Invoke(_dynamicGuiMethod.IsStatic ? null : _vrGuiInstance, null)); }
                catch (Exception e) { dynamic = "err:" + e.GetType().Name; }
            }
            string recentering = "?";
            if (_isRecenteringField != null)
            {
                try { recentering = Convert.ToString(_isRecenteringField.GetValue(_isRecenteringField.IsStatic ? null : _vrGuiInstance)); }
                catch { }
            }
            string texture = "none";
            if (_guiTextureField != null)
            {
                try
                {
                    RenderTexture rt = _guiTextureField.GetValue(_guiTextureField.IsStatic ? null : _vrGuiInstance) as RenderTexture;
                    if (rt != null) texture = rt.width + "x" + rt.height;
                }
                catch { }
            }

            // One line, every field needed to attribute the artifact. dPos/dRot are
            // the quad; dBar is AzuEPI's transform; dHudSize catches canvas resize
            // churn; dAlpha catches HUD fade. Whichever is non-zero is the cause.
            _log.LogInfo(string.Format(
                "[HudWatch] frames={0} dynamicGui={1} recentering={2} tex={3} canvas={4}/{5} " +
                "| dPos={6:F6}({7}) dRot={8:F4} dScale={9:F6} | dBar={10:F4}({11}) | dHudSize={12:F3}({13}) | alpha={14:F3} dAlpha={15:F4}({16}) " +
                "| panel={17} bar={18} group={19} " +
                "| tex={20}[{21}..{22}] texChg={23} lodBias={24:F2} lodChg={25} fpsCap={26} vSync={27} " +
                "| headAngle={28:F1} maxHead={29:F1} cam={30} " +
                "| headYaw={31:F1} headPitch={32:F1} | barPos={33} healthPos={34}",
                _frames, dynamic, recentering, texture,
                RenderModeText(), WorldCameraText(),
                _maxPosDelta, _posChanges, _maxRotDelta, _maxScaleDelta,
                _maxBarDelta, _barChanges,
                _maxHudSizeDelta, _sizeChanges,
                _prevAlpha, _maxAlphaDelta, _alphaChanges,
                _panel != null, _barRect != null, _hudGroup != null,
                QualitySettings.masterTextureLimit, _texMin, _texMax, _texChanges,
                QualitySettings.lodBias, _lodChanges,
                Application.targetFrameRate, QualitySettings.vSyncCount,
                _lastHeadAngle, _maxHeadAngle, _vrCamera != null,
                _headYaw, _headPitch,
                _barRect != null ? _barRect.anchoredPosition.ToString("F1") : "none",
                HealthPanelPosition()));

            _frames = 0;
            _maxPosDelta = _maxRotDelta = _maxScaleDelta = _maxBarDelta = _maxHudSizeDelta = _maxAlphaDelta = 0f;
            _posChanges = _barChanges = _sizeChanges = _alphaChanges = 0;
            _texChanges = 0; _lodChanges = 0; _maxHeadAngle = 0f;
            _texMin = _texMax = QualitySettings.masterTextureLimit;
        }
    }
}
