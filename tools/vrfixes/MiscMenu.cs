using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Makes mod features reachable in VR.
    //
    // The measured problem: a mod that registers its own ZInput button gets no VR binding,
    // and VHVR logs "Unmapped ZInput Key: <name>" once then adds the name to a PERMANENT
    // ignore set (VRControls.cs:521, initIgnoredZInputs :986-996) - so the feature is dead
    // for the rest of the session. 22 distinct unmapped actions were observed on this
    // install. Separately, mods bound to keyboard shortcuts have no VR path at all, which
    // is the single largest gap: VHVR patches only get_mousePosition, GetKeyDownInt and
    // GetKeyInt, and nothing injects into them.
    //
    // Rather than build a parallel UI, this injects into VHVR's existing wrist radial menu.
    // QuickAbstract already exposes a public useAsQuickAction(name, sprite, callback) and
    // already populates extraElements with non-item actions such as Sit and Map, so the
    // structure for this exists; only the entries were missing. Extra levels come from
    // paging that same radial rather than a third menu.
    internal static class MiscMenu
    {
        internal sealed class Entry
        {
            internal string Label;
            internal string Kind;
            internal string Value;
            internal string When;    // MenuContext predicate; empty means always

            // Held by the delegate handed to VHVR, so each radial slot knows which action
            // it represents. A static callback could not carry that.
            internal bool Execute()
            {
                try
                {
                    if (Kind == "zinput") return ZInputPulse.Send(Value);
                    if (Kind == "key") return KeyPulse.Send(Value);
                    if (Kind == "console") return ConsoleOpener.Open() && DirectActions.OpenConsoleKeyboard();
                    if (Kind == "emote") return DirectActions.Emote(Value);
                    if (Kind == "zoom") return DirectActions.Zoom(Value);
                    if (Kind == "chat") return DirectActions.OpenChat();
                    if (Kind == "power") return DirectActions.GuardianPower();
                    if (Kind == "panel") return DirectActions.ClosePanels();
                    if (Kind == "cmd") return DirectActions.RunCommand(Value);
                    if (Kind == "ui") return DirectActions.AdoptAndShow(Value);
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "misc action '" + Label + "' has unknown kind '" + Kind + "'");
                    return false;
                }
                catch (Exception e)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "misc action '" + Label + "' failed: " + e.Message);
                    return false;
                }
            }
        }

        private static readonly List<Entry> _entries = new List<Entry>();

        // Kinds that only make sense for an admin. Gated on ZNet.LocalPlayerIsAdminOrHost(), the
        // vanilla check the game itself uses, so a non-admin never sees a button that would be
        // refused. Evaluated per rebuild rather than cached: admin status arrives from the server
        // over RPC_AdminList after connect, so a value read at load time would be wrong.
        private static readonly HashSet<string> AdminKinds = new HashSet<string> { "console", "cmd" };
        private static MethodInfo _isAdmin;
        private static MethodInfo _serverSyncAdmin;
        private static PropertyInfo _serverSyncInstance, _serverSyncAdminProp;
        private static bool _adminResolved;
        private static int _lastAdminState = -1;

        // Admin status is read from more than one source and re-evaluated on every rebuild.
        //
        // ZNet.LocalPlayerIsAdminOrHost() is the vanilla check, but on a client it depends on the
        // admin list having arrived over RPC_AdminList, and a measured session logged admin=False on
        // a player who IS in the server's adminlist - which hid the console entirely.
        //
        // So: OR together every signal available, and if NONE of them resolves, fail OPEN. These
        // buttons only ATTEMPT a command; the server refuses a non-admin regardless, so a visible
        // button that gets refused is strictly better than a hidden button an admin needs.
        private static bool LocalPlayerIsAdmin()
        {
            if (!_adminResolved)
            {
                _adminResolved = true;
                Type znet = AccessTools.TypeByName("ZNet");
                _isAdmin = znet == null ? null : AccessTools.Method(znet, "LocalPlayerIsAdminOrHost");

                // ServerSync is bundled by most server-aware mods and syncs admin state to clients,
                // so it is often correct when the vanilla check is not yet populated.
                Type sync = AccessTools.TypeByName("ServerSync.SynchronizationManager");
                if (sync != null)
                {
                    _serverSyncInstance = sync.GetProperty("Instance", BindingFlags.Static | BindingFlags.Public);
                    _serverSyncAdminProp = sync.GetProperty("PlayerIsAdmin", BindingFlags.Instance | BindingFlags.Public);
                }
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "admin signals: ZNet=" + (_isAdmin != null) + " ServerSync=" + (_serverSyncAdminProp != null));
            }

            bool any = false;
            if (_isAdmin != null)
            {
                any = true;
                try { if (Convert.ToBoolean(_isAdmin.Invoke(null, null))) return true; } catch { }
            }
            if (_serverSyncInstance != null && _serverSyncAdminProp != null)
            {
                any = true;
                try
                {
                    object inst = _serverSyncInstance.GetValue(null, null);
                    if (inst != null && Convert.ToBoolean(_serverSyncAdminProp.GetValue(inst, null))) return true;
                }
                catch { }
            }
            return !any;   // nothing to ask -> show, and let the server decide
        }

        // The list the menu actually pages through: admin entries removed for non-admins.
        private static List<Entry> VisibleEntries()
        {
            // Gating is opt-in: see HideAdminEntriesForNonAdmins. Detection demonstrably lies on a
            // client, and hiding the admin console from an admin is a worse failure than showing a
            // button the server will refuse.
            // Evaluate the check FIRST, unconditionally. Writing this as
            //     !HideAdminEntries.Value || AdminCheck.IsAdmin()
            // short-circuited, so with hiding disabled the check never ran and never logged - which
            // meant the digit-comparison fix could not be confirmed from a session at all. The
            // detection and the decision to act on it are separate concerns.
            bool detected = AdminCheck.IsAdmin();
            bool admin = !NeuralyzeVRFixesPlugin.HideAdminEntries.Value || detected;
            var visible = new List<Entry>(_entries.Count);
            foreach (Entry e in _entries)
            {
                // "admin" is the one predicate that stays overridable, because its detection was
                // wrong for three releases and hiding the console from its own admin is worse than
                // showing a button the server refuses.
                if (e.When == "admin") { if (admin) visible.Add(e); continue; }
                if (MenuContext.Active(e.When)) visible.Add(e);
            }
            int state = admin ? 1 : 0;
            if (_lastAdminState != state)
            {
                _lastAdminState = state;
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu: admin=" + admin + ", showing " + visible.Count + " of " + _entries.Count
                    + " entries (re-evaluated each rebuild; admin arrives over RPC after connect)");
            }
            return visible;
        }
        private static bool _open, _placedOnce;
        private static int _page;
        private const int PerPage = 6;   // leaves room for Back and More in an 8-slot ring

        internal static int Count { get { return _entries.Count; } }

        // Format: Label = kind:value, comma separated. Deliberately data-driven so adding a
        // mod means adding a line, not editing code.
        internal static void Load(string spec)
        {
            _entries.Clear();
            if (string.IsNullOrEmpty(spec)) return;
            foreach (string raw in spec.Split(','))
            {
                string item = raw.Trim();
                if (item.Length == 0) continue;
                int eq = item.IndexOf('=');
                if (eq <= 0) continue;
                string label = item.Substring(0, eq).Trim();
                string action = item.Substring(eq + 1).Trim();
                int colon = action.IndexOf(':');
                if (colon <= 0) continue;
                string kind = action.Substring(0, colon).Trim().ToLowerInvariant();
                string value = action.Substring(colon + 1).Trim();

                // Optional trailing "when:<predicate>" makes the entry contextual, so the ring only
                // shows what is usable right now: "Quick Stack = azu:quickstack when:container".
                string when = "";
                int whenAt = value.LastIndexOf(" when:", StringComparison.OrdinalIgnoreCase);
                if (whenAt >= 0)
                {
                    when = value.Substring(whenAt + 6).Trim().ToLowerInvariant();
                    value = value.Substring(0, whenAt).Trim();
                }
                if (label.Length == 0 || value.Length == 0) continue;
                if (kind != "zinput" && kind != "key" && kind != "console"
                    && kind != "emote" && kind != "zoom" && kind != "chat" && kind != "power"
                    && kind != "panel" && kind != "cmd" && kind != "ui") continue;
                // console and cmd are admin surfaces by nature; an explicit when: still wins.
                if (when.Length == 0 && (kind == "console" || kind == "cmd")) when = "admin";
                _entries.Add(new Entry { Label = label, Kind = kind, Value = value, When = when });
            }
            var contexts = new List<string>();
            foreach (Entry e in _entries)
            {
                string tag = e.When.Length == 0 ? "always" : e.When;
                if (!contexts.Contains(tag)) contexts.Add(tag);
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "misc menu loaded " + _entries.Count + " action(s); contexts used: "
                + string.Join(", ", contexts.ToArray()));
        }

        internal static void Install(Harmony harmony)
        {
            try
            {
                Type qa = AccessTools.TypeByName("ValheimVRMod.Scripts.QuickAbstract");
                if (qa == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "QuickAbstract not found; misc menu unavailable");
                    return;
                }
                // Must be a PREFIX on reorderElements, not a postfix on refreshItems:
                // refreshItems calls reorderElements as its LAST statement, and
                // reorderElements is what activates and positions slots using
                // extraElementCount. Appending after refreshItems returns therefore raised
                // the count too late every single frame, so the entry was never activated.
                // reorderElements is defined on the base class, so one patch covers both hands.
                MethodInfo pre = typeof(MiscMenu).GetMethod("BeforeReorder", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo reorder = AccessTools.Method(qa, "reorderElements");
                if (reorder == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "QuickAbstract.reorderElements not found; misc menu unavailable");
                    return;
                }
                harmony.Patch(reorder, prefix: new HarmonyMethod(pre));
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu hooked into QuickAbstract.reorderElements");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "misc menu install failed: " + e.Message);
            }
        }

        private static FieldInfo _extraField, _countField;
        private static MethodInfo _useAsQuickAction, _useAsNoOp;
        private static Type _callbackType;

        private static bool Resolve(object menu)
        {
            if (_useAsQuickAction != null) return true;
            Type qa = AccessTools.TypeByName("ValheimVRMod.Scripts.QuickAbstract");
            if (qa == null) return false;
            _extraField = AccessTools.Field(qa, "extraElements");
            // reorderElements() deactivates every slot at or beyond extraElementCount, and
            // hoverItem() only scans below it. Writing into an unused slot therefore produces
            // an item that is invisible AND unselectable - it must be counted, not just filled.
            _countField = AccessTools.Field(qa, "extraElementCount");
            Type item = AccessTools.Inner(qa, "QuickMenuItem");
            if (item == null || _extraField == null) return false;
            _callbackType = AccessTools.Inner(item, "QuickMenuItemCallback");
            _useAsQuickAction = AccessTools.Method(item, "useAsQuickAction");
            _useAsNoOp = AccessTools.Method(item, "useAsNoOp");
            return _useAsQuickAction != null && _callbackType != null && _countField != null;
        }

        // VHVR fills extraElements first; we take whatever it left as no-ops. When the misc
        // page is open we own the whole ring, which is what gives the second level.
        private static void BeforeReorder(object __instance)
        {
            if (!NeuralyzeVRFixesPlugin.MiscMenuEnabled.Value) return;
            if (_entries.Count == 0) return;
            try
            {
                if (!Resolve(__instance)) return;
                Array extra = _extraField.GetValue(__instance) as Array;
                if (extra == null || extra.Length == 0) return;

                int vhvrCount = Convert.ToInt32(_countField.GetValue(__instance));
                int max = extra.Length;

                if (!_open)
                {
                    // Append after VHVR's own entries and extend the count so reorderElements
                    // activates and positions it, and hoverItem can reach it.
                    if (vhvrCount >= max) return;
                    Assign(extra.GetValue(vhvrCount), "Misc (" + VisibleEntries().Count + ")",
                        delegate { _open = true; _page = 0; return true; });
                    _countField.SetValue(__instance, vhvrCount + 1);
                    if (!_placedOnce)
                    {
                        _placedOnce = true;
                        NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                            + "misc entry appended at wrist slot " + vhvrCount + " of " + max
                            + " (VHVR used " + vhvrCount + "); it is the LAST item on that wrist strip"
                            + " - reach your other hand to it");
                    }
                    return;
                }

                // Page open: own the whole strip. refreshItems repopulates it every call, so
                // closing the page self-heals without us restoring anything.
                List<Entry> entries = VisibleEntries();
                int used = 0;
                int start = _page * PerPage;
                if (start >= entries.Count) { start = 0; _page = 0; }
                for (int i = start; i < entries.Count && used < PerPage && used < max; i++)
                {
                    Entry e = entries[i];
                    Assign(extra.GetValue(used), e.Label, e.Execute);
                    used++;
                }
                if (start + PerPage < entries.Count && used < max)
                {
                    Assign(extra.GetValue(used), "More >", delegate { _page++; return true; });
                    used++;
                }
                if (used < max)
                {
                    Assign(extra.GetValue(used), "< Back", delegate { _open = false; _page = 0; return true; });
                    used++;
                }
                _countField.SetValue(__instance, used);
                for (int i = used; i < extra.Length; i++)
                {
                    object el = extra.GetValue(i);
                    if (el != null && _useAsNoOp != null) _useAsNoOp.Invoke(el, null);
                }
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.MiscMenuEnabled.Value = false;
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "misc menu refresh failed, disabling: " + e.Message);
            }
        }

        private static void Assign(object element, string label, Func<bool> action)
        {
            if (element == null) return;
            Delegate cb = Delegate.CreateDelegate(_callbackType, action.Target, action.Method);
            // The word is baked INTO the sprite, deliberately.
            //
            // A Unity TextMesh child was tried in 2.1.82 and reverted: it inherits the item's
            // transform scale, so the text rendered far larger than the box, spilled outside it,
            // overlapped its neighbours, and stayed visible on the arm after the menu closed.
            // ResizeIcon() sizes the SpriteRenderer to the box for us, so anything drawn into the
            // texture is guaranteed to stay inside the button and to vanish with it.
            _useAsQuickAction.Invoke(element, new object[] { label, MiscLabels.For(label), cb });
        }
    }

    // Opens Valheim's dev console with the VR keyboard attached.
    //
    // Console.IsVisible() is `m_instance.m_chatWindow.gameObject.activeInHierarchy` (verified
    // IL), so showing it means activating that window. The console must also be ENABLED -
    // Console.SetConsoleEnabledForThisSession exists for exactly this. Typing then works
    // because VHVR's InputManager.start(inputField, tmpField, guiField) calls
    // SteamVR.instance.overlay.ShowKeyboard (TextInputPatches.cs:157) - the same call it uses
    // for chat and signs. That answers whether console commands can use the VR keyboard: yes,
    // through the identical path.
    internal static class ConsoleOpener
    {
        internal static bool Open()
        {
            try
            {
                Type console = AccessTools.TypeByName("Console");
                if (console == null) { Log("Console type not found"); return false; }

                // Enable for this session if it is off, else the window refuses to accept input.
                // AccessTools logged 'Could not find method ... SetConsoleEnabledForThisSession
                // and parameters (bool)' - the real overloads are not all (bool), so try the
                // no-argument shape too rather than giving up silently.
                // Verified against assembly_valheim 0.221.12:
                //   Console.SetConsoleEnabledForThisSession()  static, NO arguments
                //   Console.SetConsoleEnabled(bool)            static
                // The previous code asked for a (bool) overload of the first one, which does not
                // exist, so AccessTools logged "Could not find method" and the console opened
                // without cheats enabled - which is why `god` did nothing.
                foreach (string enabler in new[] { "SetConsoleEnabledForThisSession", "SetConsoleEnabled" })
                {
                    MethodInfo m = AccessTools.Method(console, enabler, new Type[0])
                                   ?? AccessTools.Method(console, enabler, new[] { typeof(bool) });
                    if (m == null) continue;
                    object[] args = m.GetParameters().Length == 0 ? null : new object[] { true };
                    try { m.Invoke(null, args); break; } catch { }
                }

                PropertyInfo instProp = console.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
                object inst = instProp == null ? null : instProp.GetValue(null, null);
                if (inst == null) { Log("Console.instance null - console may be disabled by the server"); return false; }

                // m_chatWindow lives on Terminal, the base class.
                Type terminal = AccessTools.TypeByName("Terminal") ?? console;
                FieldInfo winField = AccessTools.Field(terminal, "m_chatWindow");
                object win = winField == null ? null : winField.GetValue(inst);
                GameObject windowObject = null;
                if (win is GameObject) windowObject = (GameObject)win;
                else if (win is Component) windowObject = ((Component)win).gameObject;
                if (windowObject == null) { Log("console chat window not found"); return false; }
                windowObject.SetActive(true);

                // Focus its input field and raise the SteamVR keyboard.
                FieldInfo inputField = AccessTools.Field(terminal, "m_input");
                object input = inputField == null ? null : inputField.GetValue(inst);
                // Deliberately does NOT raise a keyboard here. DirectActions.OpenConsoleKeyboard
                // opens ONE keyboard, on the chat field, prefilled with "/cmd " - which is the only
                // path that actually submits. Raising one here too meant the player saw the console's
                // own keyboard first, with no prefill and no submit, and reported "I don't see a /cmd".
                Log("console enabled and window shown; keyboard is opened separately with the /cmd prefill");
                return true;
            }
            catch (Exception e) { Log("console open failed: " + e.Message); return false; }
        }

        // InputManager.start takes (InputField, TMP_InputField, GuiInputField); pass whichever
        // slot matches the field's actual type and leave the others null.
        internal static bool RaiseKeyboard(object input) { return OpenKeyboardFor(input); }

        private static bool OpenKeyboardFor(object input)
        {
            if (input == null) return false;
            try
            {
                Type im = AccessTools.TypeByName("ValheimVRMod.Patches.InputManager")
                       ?? AccessTools.TypeByName("InputManager");
                if (im == null) return false;
                foreach (MethodInfo m in im.GetMethods(BindingFlags.Static | BindingFlags.Public))
                {
                    if (m.Name != "start") continue;
                    ParameterInfo[] ps = m.GetParameters();
                    object[] args = new object[ps.Length];
                    bool placed = false;
                    for (int i = 0; i < ps.Length; i++)
                    {
                        if (!placed && ps[i].ParameterType.IsInstanceOfType(input)) { args[i] = input; placed = true; }
                        else args[i] = null;
                    }
                    if (!placed) continue;
                    m.Invoke(null, args);
                    return true;
                }
                return false;
            }
            catch { return false; }
        }

        private static void Log(string msg)
        {
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + msg);
        }
    }

    // Pulses a vanilla/mod ZInput action through VHVR's own public hook. Single-consumption,
    // which is unsuitable for held input but exactly right for a one-shot menu selection.
    internal static class ZInputPulse
    {
        private static MethodInfo _emulate;
        private static bool _dead;
        private static readonly HashSet<string> _warned = new HashSet<string>();

        internal static bool Send(string name)
        {
            if (_dead) return false;
            if (_emulate == null)
            {
                Type patch = AccessTools.TypeByName("ValheimVRMod.Patches.ZInput_GetButtonDown_Patch");
                _emulate = patch == null ? null : patch.GetMethod("EmulateButtonDown",
                    BindingFlags.Static | BindingFlags.Public, null, new[] { typeof(string) }, null);
                if (_emulate == null)
                {
                    _dead = true;
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "ZInput_GetButtonDown_Patch.EmulateButtonDown not resolvable;"
                        + " every zinput misc action is now dead");
                    return false;
                }
            }
            _emulate.Invoke(null, new object[] { name });
            // ZInput_GetButtonDown_Patch.Prefix answers true exactly once for this name, and its
            // Postfix ORs rather than overwrites, so the value does reach the caller. What is NOT
            // guaranteed is that anything ASKS: MapZoomIn/Out are only polled while the map is
            // open, Joy* names are skipped entirely when ZInput.IsGamepadEnabled() is false, and
            // mod names depend on that mod polling every frame. Delivery of the pulse is
            // therefore not evidence the action happened - which is why the direct kinds exist.
            if (_warned.Add(name))
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                    + "misc zinput '" + name + "' pulsed, but ZInput delivery is unverifiable -"
                    + " nothing may poll this name in the current game state");
            }
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "misc zinput pulse '" + name + "'");
            return true;
        }
    }

    // Injects a keyboard press so mods whose entire feature set is bound to a keyboard
    // shortcut become reachable. Two paths are needed: VHVR's own hook covers
    // ZInput.GetKeyDown, but BepInEx KeyboardShortcut and most mods read UnityEngine.Input
    // directly, which VHVR only patches at GetKeyDownInt / GetKeyInt and never injects into.
    // So we patch those two ourselves and answer true for one frame.
    internal static class KeyPulse
    {
        private static readonly HashSet<int> _pending = new HashSet<int>();
        private static readonly HashSet<int> _holdFor = new HashSet<int>();
        private static MethodInfo _vhvrEmulate;
        private static bool _installed;

        internal static void Install(Harmony harmony)
        {
            if (_installed) return;
            _installed = true;
            try
            {
                // UnityEngine.Input is type-forwarded to UnityEngine.InputLegacyModule,
                // which is not among the available reference assemblies, so it is resolved
                // at runtime rather than compiled against.
                Type input = AccessTools.TypeByName("UnityEngine.Input");
                if (input == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "UnityEngine.Input not resolvable; keyboard-bound mod actions stay unreachable");
                    return;
                }
                MethodInfo down = AccessTools.Method(input, "GetKeyDownInt", new[] { typeof(KeyCode) });
                MethodInfo held = AccessTools.Method(input, "GetKeyInt", new[] { typeof(KeyCode) });
                MethodInfo pre = typeof(KeyPulse).GetMethod("PrefixDown", BindingFlags.Static | BindingFlags.NonPublic);
                MethodInfo preHeld = typeof(KeyPulse).GetMethod("PrefixHeld", BindingFlags.Static | BindingFlags.NonPublic);
                int n = 0;
                if (down != null) { harmony.Patch(down, prefix: new HarmonyMethod(pre)); n++; }
                if (held != null) { harmony.Patch(held, prefix: new HarmonyMethod(preHeld)); n++; }
                NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                    + "keyboard pulse installed on " + n + "/2 Input entry point(s)"
                    + (n == 2 ? "" : " - some mod hotkeys may stay unreachable"));
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "keyboard pulse install failed: " + e.Message);
            }
        }

        private static bool PrefixDown(KeyCode key, ref bool __result)
        {
            if (!_pending.Remove((int)key)) return true;
            __result = true;
            _holdFor.Add((int)key);
            return false;
        }

        private static bool PrefixHeld(KeyCode key, ref bool __result)
        {
            // Some mods test IsDown() as "key held this frame"; answer once so a chord
            // built from KeyboardShortcut still resolves, then stop.
            if (!_holdFor.Remove((int)key)) return true;
            __result = true;
            return false;
        }

        internal static bool Send(string spec)
        {
            bool any = false;
            foreach (string part in spec.Split('+'))
            {
                string k = part.Trim();
                if (k.Length == 0) continue;
                try
                {
                    KeyCode code = (KeyCode)Enum.Parse(typeof(KeyCode), k, true);
                    _pending.Add((int)code);
                    if (_vhvrEmulate == null)
                    {
                        Type patch = AccessTools.TypeByName("ValheimVRMod.Patches.ZInput_GetKeyDown_Patch");
                        _vhvrEmulate = patch == null ? null : patch.GetMethod("EmulateKeyDown",
                            BindingFlags.Static | BindingFlags.Public, null, new[] { typeof(KeyCode) }, null);
                    }
                    if (_vhvrEmulate != null) _vhvrEmulate.Invoke(null, new object[] { code });
                    any = true;
                }
                catch
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "unknown KeyCode '" + k + "'");
                }
            }
            if (any) NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "misc key pulse '" + spec + "'");
            return any;
        }
    }
}
