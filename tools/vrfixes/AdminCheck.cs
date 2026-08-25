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
        // THIS IS THE 1146ms FRAME. Measured 2026-08-25 from the operator's own session
        // (LogOutput.log:170-187, in order, all inside one frame):
        //
        //   170  AccessTools.Method: Could not find method for type ZNet and name GetLocalUser
        //   171  ... and name GetLocalPlatformUser          <- Resolve(), so _getLocalUser is null
        //   172  AccessTools.GetTypesFromAssembly: assembly AzuAntiArthriticCrafting => Reflection-
        //        TypeLoadException ... Auga.CraftingControls:InputAmount ... 'ui_lib' (x2, with a
        //        native Assembly.GetTypes stack each)
        //   184  AccessTools.TypeByName: Could not find type named PrivilegeManager
        //   185  wrist menu WITHHELD 2 of 27                <- ActionAvailability.Refresh, AFTER
        //   187  misc ring rebuild took 1146.5ms: visibleEntries=1140.6
        //
        // So the second and a bit did NOT come from ActionAvailability.Refresh, which had not even
        // run yet when it was spent, and which for this config is 27 dictionary lookups (no entry
        // uses the '*' form, so GetPrefabNames is never called). It came from HERE: AccessTools
        // .TypeByName exhausting every loaded assembly with Assembly.GetTypes() looking for
        // "PrivilegeManager", which this install does not have. AdminCheck.IsAdmin() is called at
        // MiscMenu.cs:243, inside the phase the log calls visibleEntries.
        //
        // Measured on this box against the operator's exact 111-plugin profile plus the game's
        // Managed directory - 244 assemblies in the domain, 47405 types, 104 of them throwing
        // ReflectionTypeLoadException:
        //
        //   AccessTools.TypeByName("PrivilegeManager")   2793.8 ms first, 591.4 ms repeated
        //   AccessTools.TypeByName("ZSteamSocket")          5.8 ms  (a hit; stops at the assembly)
        //   one Assembly.GetTypes() sweep of all 244       76133 ms cold, 230-250 ms warm
        //   Assembly.GetType(name) sweep of all 244          7.6 ms first, 0.32 ms steady
        //   Assembly.GetType(name) sweep, a hit             0.06-0.09 ms
        //
        // Hence this resolver. Assembly.GetType is a name lookup in one assembly's type table;
        // GetTypes() materialises every type in it, which is what costs the second and what raises
        // the TypeLoadExceptions at :172-183. Nothing here needs a full type list: all three names
        // asked for below are engine or game types with an exact namespace-qualified spelling, so
        // the cheap lookup answers the same question. TypeCache is deliberately NOT changed - its
        // callers ask for mod types by partial name, which is what TypeByName is for.
        private static readonly Dictionary<string, Type> _typeCache = new Dictionary<string, Type>();

        private static Type CachedType(string name)
        {
            Type t;
            if (_typeCache.TryGetValue(name, out t)) return t;
            t = ExactType(name);
            _typeCache[name] = t;
            if (t == null)
            {
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "type '" + name + "' is not present in this modpack; remembering that so the"
                    + " lookup is not repeated");
            }
            return t;
        }

        // Fully-qualified name, asked of each assembly's type table rather than of its type list.
        private static Type ExactType(string name)
        {
            Type t = Type.GetType(name, false);
            if (t != null) return t;
            Assembly[] loaded = AppDomain.CurrentDomain.GetAssemblies();
            for (int i = 0; i < loaded.Length; i++)
            {
                try
                {
                    t = loaded[i].GetType(name, false);
                    if (t != null) return t;
                }
                catch { }   // a broken assembly answers for itself only, and is skipped
            }
            return null;
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

        // FIRST resolution, deliberately not on a menu-open frame.
        //
        // ExactType above turns the one-off cost from 1140.6 ms into a measured 7.6 ms, but 7.6 ms
        // is still half a 72Hz frame and the frame it used to land on was the worst one available:
        // the operator pressing a button and watching a menu appear. Called once from LateUpdate,
        // which is already gated on InWorld() - a local player, a ZNetScene and a Hud - so it
        // spends that half frame on an ordinary in-world frame instead, and the admin verdict is in
        // the log from world entry rather than from whenever the wrist strip is first opened.
        private static bool _warmed;

        internal static void Warm()
        {
            if (_warmed) return;
            _warmed = true;
            IsAdmin();
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
