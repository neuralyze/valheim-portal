using System;
using System.Collections.Generic;
using System.Collections;
using System.Reflection;
using System.Text;
using HarmonyLib;

namespace NeuralyzeVRFixes
{
    // Works out whether the local player is an admin, and reports WHY.
    //
    // ZNet.LocalPlayerIsAdminOrHost() returned false for a player who is in the server's adminlist.
    // Decompiling it explains the failure:
    //
    //     LocalPlayerIsAdminOrHost() => IsServer() || PlayerIsAdmin(GetLocalUser().UserId)
    //     PlayerIsAdmin(id)          => GetAdminList().Contains(id.ToString())
    //     GetAdminList()             => m_adminListForRpc          // sent by the server via RPC_AdminList
    //
    // The data IS on the client - the server pushes its adminlist over RPC_AdminList. The comparison
    // is the problem: it is an EXACT string match between the list's entries and
    // PlatformUserID.ToString(). A dedicated server configured with bare numeric IDs
    // (ADMINLIST_IDS="7656119...") stores those bare digits, while ToString() renders a
    // platform-qualified form, so the exact match fails and a real admin reads as a normal player.
    //
    // This compares DIGITS ONLY, which matches either format, and logs both sides once so the
    // discrepancy is visible rather than inferred.
    internal static class AdminCheck
    {
        private static MethodInfo _isServer, _getAdminList, _getLocalUser;
        private static PropertyInfo _znetInstance;
        private static bool _resolved, _reported;
        private static int _last = -1;

        // Everything below exists to make IsAdmin() O(1) after the first successful read.
        private static string _cachedId;          // digits of the local user id, "" = not found yet
        private static int _idAttempts;           // hard cap: probing is expensive and may throw
        private const int MaxIdAttempts = 3;
        private static bool _cachedVerdict;
        private static float _verdictAt = -999f;
        private const float VerdictTtlSeconds = 2f;

        private static void Resolve()
        {
            if (_resolved) return;
            _resolved = true;
            Type znet = TypeCache.Get("ZNet");
            if (znet == null) return;
            _znetInstance = znet.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
            _isServer = AccessTools.Method(znet, "IsServer");
            _getAdminList = AccessTools.Method(znet, "GetAdminList");
            // Present but awkward to name-match; take whatever exposes the local user.
            _getLocalUser = AccessTools.Method(znet, "GetLocalUser")
                            ?? AccessTools.Method(znet, "GetLocalPlatformUser");
        }

        private static object Znet()
        {
            Resolve();
            try { return _znetInstance == null ? null : _znetInstance.GetValue(null, null); }
            catch { return null; }
        }

        // Keeps only 0-9, so "Steam_76561197987967077" and "76561197987967077" compare equal.
        private static string Digits(string value)
        {
            if (string.IsNullOrEmpty(value)) return "";
            var sb = new StringBuilder(value.Length);
            foreach (char c in value) if (c >= '0' && c <= '9') sb.Append(c);
            return sb.ToString();
        }

        // Several sources, because ZNet.GetLocalUser() reflection came back empty on a live client
        // ("admin check: False (local user id unreadable)") even though the IL calls it.
        // Caches the answer, and gives up after a few tries. The expensive path enumerates methods
        // on three types and invokes candidates; repeating that forever is what produced the stalls.
        private static float _idSearchedAt;

        private static string CachedLocalUserId(object znet)
        {
            if (!string.IsNullOrEmpty(_cachedId)) return _cachedId;
            // A failed search is expensive; retry it every thirty seconds, not every two.
            float now = UnityEngine.Time.realtimeSinceStartup;
            if (_idSearchedAt > 0f && now - _idSearchedAt < 30f) return "";
            _idSearchedAt = now;
            if (_idAttempts >= MaxIdAttempts) return "";
            _idAttempts++;
            string found = Digits(LocalUserIdAnySource(znet));
            if (found.Length > 0)
            {
                _cachedId = found;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "local user id resolved after " + _idAttempts + " attempt(s)");
            }
            else if (_idAttempts >= MaxIdAttempts)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "local user id unreadable after " + MaxIdAttempts + " attempts; giving up rather than"
                    + " probing every frame. Admin entries stay visible.");
            }
            return _cachedId ?? "";
        }

        // Resolved names, INCLUDING the ones that do not resolve.
        //
        // AccessTools.TypeByName returns null by exhausting every assembly, so a name that is not
        // present costs far more than one that is - and this list is walked on every failed attempt
        // to read the local Steam id. Remembering the null is the whole fix.
        private static readonly Dictionary<string, Type> _typeCache = new Dictionary<string, Type>();

        private static Type CachedType(string name)
        {
            Type t;
            if (_typeCache.TryGetValue(name, out t)) return t;
            t = TypeCache.Get(name);
            _typeCache[name] = t;
            if (t == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "type '" + name + "' is not present in this modpack; remembering that so the"
                    + " assembly scan is not repeated");
            }
            return t;
        }

        private static string LocalUserIdAnySource(object znet)
        {
            string viaZnet = LocalUserId(znet);
            if (Digits(viaZnet).Length > 0) return viaZnet;

            foreach (string typeName in new[] { "PrivilegeManager", "ZSteamSocket", "Steamworks.SteamUser" })
            {
                Type t = CachedType(typeName);
                if (t == null) continue;
                foreach (MethodInfo m in t.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic))
                {
                    if (m.GetParameters().Length != 0) continue;
                    if (m.Name.IndexOf("SteamID", StringComparison.OrdinalIgnoreCase) < 0
                        && m.Name.IndexOf("LocalUser", StringComparison.OrdinalIgnoreCase) < 0
                        && m.Name.IndexOf("UserId", StringComparison.OrdinalIgnoreCase) < 0) continue;
                    try
                    {
                        object v = m.Invoke(null, null);
                        string digits = Digits(Convert.ToString(v));
                        if (digits.Length >= 15)
                        {
                            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                                + "local user id via " + typeName + "." + m.Name);
                            return digits;
                        }
                    }
                    catch { }
                }
            }
            return "";
        }

        private static string LocalUserId(object znet)
        {
            if (_getLocalUser == null) return "";
            try
            {
                object user = _getLocalUser.Invoke(znet, null);
                if (user == null) return "";
                // UserId may be a field or property depending on version; ToString() is the fallback.
                FieldInfo idField = AccessTools.Field(user.GetType(), "UserId");
                if (idField != null) return Convert.ToString(idField.GetValue(user));
                PropertyInfo idProp = user.GetType().GetProperty("UserId");
                if (idProp != null) return Convert.ToString(idProp.GetValue(user, null));
                return user.ToString();
            }
            catch { return ""; }
        }

        internal static bool IsAdmin()
        {
            // Re-evaluated periodically rather than per call. Admin status arrives once, over RPC,
            // so a 2 second staleness is irrelevant - whereas asking on every menu rebuild was
            // costing whole seconds of frame time.
            float now = UnityEngine.Time.realtimeSinceStartup;
            if (now - _verdictAt < VerdictTtlSeconds) return _cachedVerdict;
            _verdictAt = now;
            _cachedVerdict = Evaluate();
            return _cachedVerdict;
        }

        private static bool Evaluate()
        {
            object znet = Znet();
            if (znet == null) return false;

            try { if (_isServer != null && Convert.ToBoolean(_isServer.Invoke(znet, null))) return Report(true, "local player is the host"); }
            catch { }

            IList admins = null;
            try { admins = _getAdminList == null ? null : _getAdminList.Invoke(znet, null) as IList; }
            catch { }

            string me = CachedLocalUserId(znet);
            if (admins == null) return Report(false, "server has not sent an admin list yet");
            if (me.Length == 0) return Report(false, "local user id unreadable");

            var listed = new StringBuilder();
            bool match = false;
            foreach (object entry in admins)
            {
                string raw = Convert.ToString(entry);
                if (listed.Length > 0) listed.Append(',');
                listed.Append(raw);
                if (Digits(raw) == me) match = true;
            }
            return Report(match, "me=" + me + " adminList=[" + listed + "] entries=" + admins.Count);
        }

        private static bool Report(bool admin, string why)
        {
            int state = admin ? 1 : 0;
            if (state != _last || !_reported)
            {
                _last = state;
                _reported = true;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "admin check: " + admin + " (" + why + ")");
            }
            return admin;
        }
    }
}

namespace NeuralyzeVRFixes
{
    // Sets the SteamVR scene render scale, which is the only resolution lever that works on this
    // pipeline.
    //
    // XRSettings.renderViewportScale reads 0 here and does nothing - Valheim renders through
    // OpenVR/SteamVR, not Unity's XR display subsystem, so the earlier RenderScale setting was
    // inert. The value that matters is Valve's SteamVR_Camera.sceneResolutionScale.
    //
    // Measured need: the diagnostics probe reports a per-eye target of 1933x2066 - 4.0 MPix per eye,
    // 8.0 MPix per frame - at 15-20 ms of GPU time. Pixels are the dominant GPU cost, so scale is
    // the biggest single GPU lever available; 0.85 removes ~28% of them.
    internal static class SteamVrRenderScale
    {
        internal static void Apply()
        {
            float wanted = NeuralyzeVRFixesPlugin.SteamVrScale.Value;
            if (wanted <= 0f) return;      // 0 = leave SteamVR alone

            try
            {
                Type cam = TypeCache.Get("Valve.VR.SteamVR_Camera");
                if (cam == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "SteamVR_Camera not found; render scale not applied");
                    return;
                }
                PropertyInfo prop = cam.GetProperty("sceneResolutionScale",
                    BindingFlags.Static | BindingFlags.Public);
                if (prop == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "SteamVR_Camera.sceneResolutionScale not found; render scale not applied");
                    return;
                }
                float before = Convert.ToSingle(prop.GetValue(null, null));
                prop.SetValue(null, wanted, null);
                float after = Convert.ToSingle(prop.GetValue(null, null));
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "SteamVR sceneResolutionScale " + before.ToString("F3") + " -> " + after.ToString("F3")
                    + " (requested " + wanted.ToString("F3") + ")"
                    + (Math.Abs(after - wanted) > 0.001f ? " - REFUSED, SteamVR kept its own value" : ""));
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "render scale failed: " + e.Message);
            }
        }
    }
}
