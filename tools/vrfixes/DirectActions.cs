using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace NeuralyzeVRFixes
{
    // Calls the game directly instead of synthesising input.
    //
    // Written after a measured session in which all eight wrist-menu actions provably fired
    // (`misc zinput pulse '<name>'` x8 in the log) and NOTHING happened in game. The pulse
    // reaches ZInput.GetButtonDown correctly - VHVR's Prefix answers true once and its Postfix
    // ORs the result - but a value nobody reads is not an action. Each of those names has its
    // own gate: MapZoomIn/Out are polled only while the map is open, Joy* names are skipped
    // when ZInput.IsGamepadEnabled() is false, and mod names need that mod polling every frame.
    //
    // Every method here reports what it did, because the previous design's silence is what
    // burned a whole test session.
    internal static class DirectActions
    {
        private static void Log(string msg)
        {
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag + "misc " + msg);
        }

        private static void Warn(string msg)
        {
            NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "misc " + msg);
        }

        private static object Instance(string typeName)
        {
            Type t = AccessTools.TypeByName(typeName);
            if (t == null) return null;
            PropertyInfo p = t.GetProperty("instance", BindingFlags.Static | BindingFlags.Public);
            if (p != null) return p.GetValue(null, null);
            FieldInfo f = AccessTools.Field(t, "m_instance") ?? AccessTools.Field(t, "instance");
            return f == null ? null : f.GetValue(null);
        }

        // Emotes are chat commands in Valheim - there is no emote API to call. Chat.SendText
        // takes (Talker.Type, string), so the enum is resolved by NAME rather than by an
        // assumed ordinal: Talker.Type is not documented to start at Normal.
        internal static bool Emote(string name)
        {
            try
            {
                object chat = Instance("Chat");
                if (chat == null) { Warn("emote '" + name + "': Chat.instance null"); return false; }
                Type talker = AccessTools.TypeByName("Talker");
                Type kind = talker == null ? null : AccessTools.Inner(talker, "Type");
                MethodInfo send = AccessTools.Method(chat.GetType(), "SendText");
                if (kind == null || send == null)
                {
                    Warn("emote '" + name + "': Chat.SendText/Talker.Type not resolvable");
                    return false;
                }
                object normal = Enum.Parse(kind, "Normal", true);
                send.Invoke(chat, new object[] { normal, "/" + name });
                Log("emote '/" + name + "' sent");
                return true;
            }
            catch (Exception e) { Warn("emote '" + name + "' failed: " + e.Message); return false; }
        }

        // Minimap keeps SEPARATE zoom values for the corner and full-screen views
        // (m_smallZoom / m_largeZoom) and picks by m_mode, so writing the wrong one silently
        // does nothing visible. Smaller zoom value = closer in.
        internal static bool Zoom(string direction)
        {
            try
            {
                object map = Instance("Minimap");
                if (map == null) { Warn("zoom: Minimap.instance null"); return false; }
                Type t = map.GetType();
                FieldInfo modeField = AccessTools.Field(t, "m_mode");
                string mode = modeField == null ? "?" : Convert.ToString(modeField.GetValue(map));
                bool large = mode != null && mode.IndexOf("Large", StringComparison.OrdinalIgnoreCase) >= 0;
                FieldInfo zoomField = AccessTools.Field(t, large ? "m_largeZoom" : "m_smallZoom");
                if (zoomField == null) { Warn("zoom: zoom field missing"); return false; }

                float zoom = Convert.ToSingle(zoomField.GetValue(map));
                float min = ReadFloat(t, map, "m_minZoom", 0.01f);
                float max = ReadFloat(t, map, "m_maxZoom", 1.0f);
                bool inward = direction.IndexOf("in", StringComparison.OrdinalIgnoreCase) >= 0;
                float next = Mathf.Clamp(inward ? zoom * 0.6f : zoom / 0.6f, min, max);
                zoomField.SetValue(map, next);
                if (Mathf.Approximately(next, zoom))
                {
                    Log("zoom " + direction + " already at the " + (inward ? "closest" : "widest")
                        + " limit (" + next.ToString("F3") + ", range " + min.ToString("F3") + ".." + max.ToString("F3") + ")");
                    return true;
                }
                Log("zoom " + direction + " on the " + (large ? "full-screen" : "corner") + " map: "
                    + zoom.ToString("F3") + " -> " + next.ToString("F3"));
                return true;
            }
            catch (Exception e) { Warn("zoom failed: " + e.Message); return false; }
        }

        private static float ReadFloat(Type t, object target, string field, float fallback)
        {
            FieldInfo f = AccessTools.Field(t, field);
            if (f == null) return fallback;
            try { return Convert.ToSingle(f.GetValue(target)); } catch { return fallback; }
        }

        // Same shape as the console opener: show the window, focus the field, raise the SteamVR
        // keyboard. Chat is the one text surface VHVR already supports, so this is the path
        // most likely to work.
        // Opens the VR keyboard through VHVR's OWN chat path, which is the only route that
        // actually submits what you type.
        //
        // InputManager.OnKeyboardClosed only acts on the typed text when
        // QuickAbstract.shouldStartChat is set:
        //
        //     if (text.StartsWith("/cmd")) Console.instance.TryRunCommand(text.Remove(0, 5));
        //     else { Chat.instance.m_input.text = text; Chat.instance.InputText(); }
        //
        // Earlier versions raised the keyboard without setting that flag, so the text landed in the
        // field and was never submitted - which is exactly why typing `god` did nothing.
        //
        // `prefill` seeds the field, and ShowKeyboard is given the field's current text, so passing
        // "/cmd " makes everything typed afterwards run as a console command.
        internal static bool OpenChat() { return OpenKeyboard("", "chat"); }

        internal static bool OpenConsoleKeyboard()
        {
            MarkPlayerIssued();
            return OpenKeyboard("/cmd ", "console");
        }

        private static bool OpenKeyboard(string prefill, string what)
        {
            try
            {
                object chat = Instance("Chat");
                if (chat == null) { Warn(what + ": Chat.instance null"); return false; }

                Type quick = AccessTools.TypeByName("ValheimVRMod.Scripts.QuickAbstract");
                FieldInfo flag = quick == null ? null : AccessTools.Field(quick, "shouldStartChat");
                if (flag == null)
                {
                    Warn(what + ": QuickAbstract.shouldStartChat not found - typed text would be discarded");
                    return false;
                }

                FieldInfo inputField = AccessTools.Field(chat.GetType(), "m_input")
                                       ?? AccessTools.Field(AccessTools.TypeByName("Terminal"), "m_input");
                object input = inputField == null ? null : inputField.GetValue(chat);
                if (input == null) { Warn(what + ": chat input field not found"); return false; }

                PropertyInfo textProp = input.GetType().GetProperty("text");
                if (textProp != null) textProp.SetValue(input, prefill, null);

                flag.SetValue(null, true);
                bool keyboard = ConsoleOpener.RaiseKeyboard(input);
                Log(what + " keyboard " + (keyboard ? "opened" : "FAILED to open")
                    + (prefill.Length > 0 ? " prefilled with '" + prefill + "'" : "")
                    + " - submits on keyboard close");
                return keyboard;
            }
            catch (Exception e) { Warn(what + " keyboard failed: " + e.Message); return false; }
        }

        internal static bool GuardianPower()
        {
            try
            {
                Type player = AccessTools.TypeByName("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) { Warn("power: no local player"); return false; }
                MethodInfo start = AccessTools.Method(player, "StartGuardianPower");
                if (start == null) { Warn("power: Player.StartGuardianPower missing"); return false; }
                object ok = start.Invoke(local, null);
                Log("guardian power -> " + (ok == null ? "invoked" : ok.ToString()));
                return true;
            }
            catch (Exception e) { Warn("power failed: " + e.Message); return false; }
        }

        // Escape hatch for an adopted mod panel that has taken the screen and will not give it back.
        // Reported symptom: an AdminQoL window opened, rendered partly off the panel, could not be
        // closed, and blocked thumbstick movement - which ended a test session.
        //
        // Three attempts, cheapest first, each reported so the log says which one worked:
        //   1. every canvas we adopted that is currently active gets deactivated
        //   2. Menu.instance is hidden if it is showing
        //   3. an Escape key pulse, which is what most mod panels actually listen for
        internal static bool ClosePanels()
        {
            int closed = 0;
            try
            {
                foreach (string name in VRGuiBridge.AdoptedNames())
                {
                    GameObject go = GameObject.Find(name);
                    if (go == null || !go.activeSelf) continue;
                    go.SetActive(false);
                    closed++;
                    Log("closed adopted canvas '" + name + "'");
                }
            }
            catch (Exception e) { Warn("canvas close failed: " + e.Message); }

            try
            {
                Type menu = AccessTools.TypeByName("Menu");
                object inst = menu == null ? null : Instance("Menu");
                MethodInfo visible = menu == null ? null : AccessTools.Method(menu, "IsVisible");
                MethodInfo hide = menu == null ? null : AccessTools.Method(menu, "Hide");
                bool showing = visible != null && inst != null && Convert.ToBoolean(visible.Invoke(null, null));
                if (showing && hide != null) { hide.Invoke(inst, null); closed++; Log("hid the main menu"); }
            }
            catch (Exception e) { Warn("menu hide failed: " + e.Message); }

            // The console and chat windows are NOT adopted canvases, so the loop above never saw
            // them - yet an open console blocks Player.TakeInput(), which is what left the player
            // unable to move with no obvious way out.
            foreach (string owner in new[] { "Console", "Chat" })
            {
                try
                {
                    object inst = Instance(owner);
                    if (inst == null) continue;
                    Type t = AccessTools.TypeByName("Terminal") ?? inst.GetType();
                    FieldInfo win = AccessTools.Field(t, "m_chatWindow") ?? AccessTools.Field(inst.GetType(), "m_chatWindow");
                    object value = win == null ? null : win.GetValue(inst);
                    GameObject go = value as GameObject;
                    if (go == null && value is Component) go = ((Component)value).gameObject;
                    if (go == null || !go.activeSelf) continue;
                    go.SetActive(false);
                    closed++;
                    Log("closed the " + owner.ToLowerInvariant() + " window");
                }
                catch (Exception e) { Warn("closing " + owner + " failed: " + e.Message); }
            }

            // In VR, an ACTIVE screen-space canvas is by definition not visible to the player:
            // VHVR converts the canvases it knows about to world space, so anything still rendering
            // in screen space goes to the desktop mirror only - which is exactly what was reported,
            // a mod window "rendered to the desktop but not VR" while still holding input and
            // leaving the cursor stuck.
            //
            // Only reached when the player presses Close Panel, so this is an explicit rescue rather
            // than a background policy, and every name is logged so the damage is inspectable.
            try
            {
                foreach (Canvas canvas in UnityEngine.Object.FindObjectsOfType<Canvas>())
                {
                    if (canvas == null || !canvas.gameObject.activeInHierarchy) continue;
                    if (canvas.renderMode == RenderMode.WorldSpace) continue;   // VHVR handles these
                    canvas.gameObject.SetActive(false);
                    closed++;
                    Log("deactivated screen-space canvas '" + canvas.name + "' (invisible in VR)");
                }
            }
            catch (Exception e) { Warn("screen-space sweep failed: " + e.Message); }

            bool esc = KeyPulse.Send("Escape");
            Log("close: " + closed + " panel(s) deactivated, escape pulse " + (esc ? "sent" : "unavailable"));
            return closed > 0 || esc;
        }

        // Runs a console command WITHOUT typing it.
        //
        // A VR session established that typing is not a workable path: the SteamVR keyboard opened,
        // "god" was entered, no output was visible, the command did not take effect, and the player
        // was then stuck because an open console blocks Player.TakeInput(). Two separate causes -
        // cheats were never enabled (the enabler was called with the wrong signature) and closing
        // the keyboard does not necessarily submit to Terminal.
        //
        // Terminal.TryRunCommand(string, bool, bool) executes directly, so a menu entry is both
        // more reliable and faster than a keyboard.
        internal static bool RunCommand(string command)
        {
            if (string.IsNullOrEmpty(command)) return false;
            MarkPlayerIssued();
            try
            {
                Type console = AccessTools.TypeByName("Console");
                object term = Instance("Console");
                if (console == null || term == null)
                {
                    Warn("cmd '" + command + "': no console instance - the server may have it disabled");
                    return false;
                }

                // Cheats are gated on the console being enabled for the session; without this the
                // command parses and silently refuses.
                foreach (string enabler in new[] { "SetConsoleEnabledForThisSession", "SetConsoleEnabled" })
                {
                    MethodInfo m = AccessTools.Method(console, enabler, new Type[0])
                                   ?? AccessTools.Method(console, enabler, new[] { typeof(bool) });
                    if (m == null) continue;
                    try { m.Invoke(null, m.GetParameters().Length == 0 ? null : new object[] { true }); break; }
                    catch { }
                }

                Type terminal = AccessTools.TypeByName("Terminal") ?? console;
                MethodInfo run = AccessTools.Method(terminal, "TryRunCommand",
                    new[] { typeof(string), typeof(bool), typeof(bool) });
                if (run == null)
                {
                    Warn("cmd '" + command + "': Terminal.TryRunCommand(string,bool,bool) not found");
                    return false;
                }
                int before = BufferLength(terminal, term);
                run.Invoke(term, new object[] { command, false, false });

                // Output goes into Terminal.m_chatBuffer, which is rendered inside the console
                // window - and that window is frequently off-view or clipped in VR. Echo the new
                // lines into the log so the result is recoverable even when it cannot be read
                // in the headset.
                string tail = BufferTail(terminal, term, before);
                Log("cmd '" + command + "' ran" + (tail.Length == 0 ? " (no output)" : " -> " + tail));
                return true;
            }
            catch (Exception e) { Warn("cmd '" + command + "' failed: " + e.Message); return false; }
        }

        private static int BufferLength(Type terminal, object term)
        {
            try
            {
                var list = AccessTools.Field(terminal, "m_chatBuffer").GetValue(term) as System.Collections.IList;
                return list == null ? 0 : list.Count;
            }
            catch { return 0; }
        }

        private static string BufferTail(Type terminal, object term, int from)
        {
            try
            {
                var list = AccessTools.Field(terminal, "m_chatBuffer").GetValue(term) as System.Collections.IList;
                if (list == null || list.Count <= from) return "";
                var text = new System.Text.StringBuilder();
                for (int i = from; i < list.Count && i < from + 6; i++)
                {
                    if (text.Length > 0) text.Append(" / ");
                    text.Append(Convert.ToString(list[i]));
                }
                return text.ToString();
            }
            catch { return ""; }
        }

        // Mirrors EVERY console command's output into the BepInEx log, however it was issued -
        // typed through the VR keyboard, run by a cmd: button, or triggered by another mod.
        //
        // Terminal renders its output from m_chatBuffer inside the console window, and that window
        // is routinely clipped or off-view in VR, so the result of a command was simply unreadable.
        internal static void InstallCommandEcho(Harmony harmony)
        {
            try
            {
                Type terminal = AccessTools.TypeByName("Terminal");
                MethodInfo run = terminal == null ? null : AccessTools.Method(terminal, "TryRunCommand",
                    new[] { typeof(string), typeof(bool), typeof(bool) });
                if (run == null)
                {
                    NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag
                        + "Terminal.TryRunCommand not found; console output stays invisible in VR");
                    return;
                }
                harmony.Patch(run,
                    prefix: new HarmonyMethod(typeof(DirectActions).GetMethod("BeforeCommand", BindingFlags.Static | BindingFlags.NonPublic)),
                    postfix: new HarmonyMethod(typeof(DirectActions).GetMethod("AfterCommand", BindingFlags.Static | BindingFlags.NonPublic)));
                ProbeHealth.Announce("ConsoleEcho", true, "console output mirrored to this log");
            }
            catch (Exception e)
            {
                NeuralyzeVRFixesPlugin.Log.LogWarning(NeuralyzeVRFixesPlugin.Tag + "console echo install failed: " + e.Message);
            }
        }

        private static int _bufferBefore;

        // Only commands the player issued get put on screen. ServerDevcommands registers ~50 aliases
        // through TryRunCommand at boot ("alias tool_area hammer from=<x>,<z>,<y> circle=..."), and
        // echoing each of those to MessageHud painted parameter text over the whole screen including
        // the loading screen. The log echo stays unconditional - it is free and it is what let me
        // find this - but the HUD and chat only show what was asked for.
        private static double _playerIssuedUntil;

        internal static void MarkPlayerIssued()
        {
            _playerIssuedUntil = UnityEngine.Time.realtimeSinceStartupAsDouble + 30.0;
        }

        private static bool PlayerIssued()
        {
            return UnityEngine.Time.realtimeSinceStartupAsDouble <= _playerIssuedUntil;
        }

        private static void BeforeCommand(object __instance)
        {
            _bufferBefore = BufferLength(AccessTools.TypeByName("Terminal"), __instance);
        }

        private static void AfterCommand(object __instance, string text)
        {
            Type terminal = AccessTools.TypeByName("Terminal");
            string tail = BufferTail(terminal, __instance, _bufferBefore);
            NeuralyzeVRFixesPlugin.Log.LogInfo(NeuralyzeVRFixesPlugin.Tag
                + "CONSOLE '" + text + "' -> " + (tail.Length == 0 ? "(no output)" : tail));

            // The console's own window keeps a screen-space pixel rect in world space, so in VR its
            // output is clipped and effectively unreadable. Chat and MessageHud are the two text
            // surfaces VHVR is known to render correctly - chat is the path its own /cmd handler
            // already uses - so the result is echoed to both: chat for the full text and scrollback,
            // MessageHud for immediate confirmation that the command was accepted at all.
            // Valheim's `god` prints nothing to m_chatBuffer, so the honest report was
            // "god: (no output)" - which told the player nothing about whether it worked. Read the
            // resulting state back off Player instead: InGodMode/InGhostMode/IsDebugFlying are the
            // actual flags those commands toggle.
            if (!PlayerIssued()) return;
            string shown = tail.Length > 0 ? tail : CheatState();
            if (shown.Length == 0) shown = "(no output)";
            EchoToChat("> " + text, shown);
            ShowCentreMessage(text + ": " + Shorten(shown, 90));
        }

        // Reads back the cheat flags a command may have toggled, so a silent command still reports
        // an observable result rather than "(no output)".
        private static string CheatState()
        {
            try
            {
                Type player = AccessTools.TypeByName("Player");
                object local = player == null ? null : AccessTools.Field(player, "m_localPlayer").GetValue(null);
                if (local == null) return "";
                var parts = new System.Collections.Generic.List<string>();
                foreach (var probe in new[] {
                    new[] { "InGodMode", "god" },
                    new[] { "InGhostMode", "ghost" },
                    new[] { "IsDebugFlying", "fly" } })
                {
                    MethodInfo m = AccessTools.Method(player, probe[0]);
                    if (m == null) continue;
                    try { parts.Add(probe[1] + "=" + Convert.ToBoolean(m.Invoke(local, null))); }
                    catch { }
                }
                return parts.Count == 0 ? "" : string.Join(" ", parts.ToArray());
            }
            catch { return ""; }
        }

        private static string Shorten(string value, int limit)
        {
            if (string.IsNullOrEmpty(value) || value.Length <= limit) return value;
            return value.Substring(0, limit - 1) + "\u2026";
        }

        // Appends through Terminal.AddString(string) on the CHAT instance. Chat inherits it from
        // Terminal, so the lookup is on Terminal and the invocation target is Chat.instance.
        private static void EchoToChat(params string[] lines)
        {
            try
            {
                object chat = Instance("Chat");
                Type terminal = AccessTools.TypeByName("Terminal");
                MethodInfo add = terminal == null ? null : AccessTools.Method(terminal, "AddString", new[] { typeof(string) });
                if (chat == null || add == null) return;
                foreach (string line in lines)
                {
                    if (!string.IsNullOrEmpty(line)) add.Invoke(chat, new object[] { line });
                }
            }
            catch (Exception e) { Warn("chat echo failed: " + e.Message); }
        }

        // MessageHud.ShowMessage's trailing parameters have varied across game versions, so the
        // argument array is built from the actual ParameterInfo rather than a hardcoded arity.
        private static void ShowCentreMessage(string message)
        {
            try
            {
                Type hudType = AccessTools.TypeByName("MessageHud");
                object hud = hudType == null ? null : Instance("MessageHud");
                if (hud == null) return;
                MethodInfo show = AccessTools.Method(hudType, "ShowMessage");
                if (show == null) return;

                Type messageType = AccessTools.Inner(hudType, "MessageType");
                if (messageType == null) return;
                object centre = Enum.Parse(messageType, "Center", true);

                ParameterInfo[] parameters = show.GetParameters();
                object[] args = new object[parameters.Length];
                for (int i = 0; i < parameters.Length; i++)
                {
                    Type t = parameters[i].ParameterType;
                    if (t == messageType) args[i] = centre;
                    else if (t == typeof(string)) args[i] = message;
                    else if (parameters[i].HasDefaultValue) args[i] = parameters[i].DefaultValue;
                    else if (t.IsValueType) args[i] = Activator.CreateInstance(t);
                    else args[i] = null;
                }
                show.Invoke(hud, args);
            }
            catch (Exception e) { Warn("hud message failed: " + e.Message); }
        }

        // Brings ONE named mod canvas into VR on demand, and shows it.
        //
        // This is canvas adoption re-introduced deliberately, which is the only responsible way to do
        // it after the default-on list caused three regressions (a window clipped half off the panel,
        // the same window parented to a wrist, and the main menu drawn tiny and duplicated). The
        // differences that matter: it runs only when the player presses a button, it touches exactly
        // the canvas named in the config, and it logs the geometry before and after.
        //
        // The panel is activated DIRECTLY rather than by pulsing the mod's toggle key, because
        // AdminQoL's "Toggle Panel Key" is F6 - shared with PlantEasily and DonegalHorizonLift on this
        // install, so a pulse would fire all three.
        internal static bool AdoptAndShow(string canvasName)
        {
            if (string.IsNullOrEmpty(canvasName)) return false;
            try
            {
                bool converted = VRGuiBridge.Adopt(canvasName);

                GameObject panel = null;
                foreach (Canvas canvas in UnityEngine.Object.FindObjectsOfType<Canvas>(true))
                {
                    if (canvas != null && canvas.name == canvasName) { panel = canvas.gameObject; break; }
                }
                if (panel == null)
                {
                    Warn("panel '" + canvasName + "' not found - the mod may create it lazily;"
                         + " open it once by its own hotkey, then use this button");
                    return false;
                }

                // Never hide it. AdminQoL's canvas ROOT is permanently active - the panel's own
                // visibility lives on a child - so toggling the root hid the whole surface and the
                // next press reported "not found". Converting and ensuring active is all this should
                // do; the mod keeps control of whether its panel is drawn.
                bool wasActive = panel.activeSelf;
                if (!wasActive) panel.SetActive(true);
                Log("panel '" + canvasName + "' ready" + (wasActive ? " (was already active)" : " (activated)")
                    + (converted ? ", converted to VR world space" : ", already registered")
                    + " - open it with its own hotkey and point the laser at it");
                return true;
            }
            catch (Exception e) { Warn("panel '" + canvasName + "' failed: " + e.Message); return false; }
        }
    }
}